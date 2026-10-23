using System.Text.Json;
using Microsoft.Data.Sqlite;
using TraceGate.MesLab;

// This environment-configured lab does not require appsettings live reload.
// Disable file watchers by default so it also runs in restricted local sandboxes.
const string reloadSetting = "DOTNET_HOSTBUILDER__RELOADCONFIGONCHANGE";
Environment.SetEnvironmentVariable(reloadSetting, Environment.GetEnvironmentVariable(reloadSetting) ?? "false");
var builder = WebApplication.CreateBuilder(args);
if (string.IsNullOrWhiteSpace(builder.Configuration["urls"]))
    builder.WebHost.UseUrls("http://localhost:5080");
builder.Services.ConfigureHttpJsonOptions(options => options.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.CamelCase);
builder.Services.Configure<RouteHandlerOptions>(options => options.ThrowOnBadRequest = true);
builder.Services.AddSingleton<MesStore>();
var app = builder.Build();
app.Services.GetRequiredService<MesStore>().Initialize();

app.Use(async (context, next) =>
{
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    context.Response.Headers["Referrer-Policy"] = "no-referrer";
    context.Response.Headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'";
    try { await next(context); }
    catch (MesException error)
    {
        context.Response.StatusCode = error.StatusCode;
        await context.Response.WriteAsJsonAsync(new { error = new { error.Code, error.Message } });
    }
    catch (BadHttpRequestException)
    {
        context.Response.StatusCode = 400;
        await context.Response.WriteAsJsonAsync(new { error = new { code = "invalid_request", message = "Provide a valid JSON request body with the required fields." } });
    }
    catch (SqliteException error) when (error.SqliteErrorCode is 5 or 6)
    {
        context.Response.StatusCode = 503;
        await context.Response.WriteAsJsonAsync(new { error = new { code = "database_busy", message = "The demo database is busy. Retry using the same request ID." } });
    }
    catch (Exception error)
    {
        app.Logger.LogError(error, "Request failed: {Method} {Path}", context.Request.Method, context.Request.Path);
        context.Response.StatusCode = 500;
        await context.Response.WriteAsJsonAsync(new { error = new { code = "server_error", message = "The request could not be completed." } });
    }
});
app.UseDefaultFiles();
app.UseStaticFiles();

app.MapGet("/api/health", (MesStore store) => { store.CheckHealth(); return Results.Ok(new { status = "ok", database = "sqlite", mode = "synthetic-demo" }); });
app.MapGet("/api/meta", () => new { appName = "TraceGate MES Lab", route = MesStore.Route, syntheticData = true });
app.MapGet("/api/dashboard", (MesStore store) => store.Dashboard());
app.MapGet("/api/work-orders", (MesStore store) => new { items = store.WorkOrders() });
app.MapPost("/api/work-orders", (WorkOrderInput input, MesStore store, HttpResponse response) => WriteMutation(store.CreateWorkOrder(input), response));
app.MapGet("/api/work-orders/{no}/units", (string no, MesStore store) => new { items = store.Units(no) });
app.MapPost("/api/units/register", (RegisterInput input, MesStore store, HttpResponse response) => WriteMutation(store.Register(input), response));
app.MapGet("/api/units/{sn}/trace", (string sn, MesStore store) => store.Trace(sn));
app.MapPost("/api/units/{sn}/station-events", (string sn, StationInput input, MesStore store, HttpResponse response) => WriteMutation(store.Report(sn, input), response));
app.MapPost("/api/units/{sn}/release-hold", (string sn, ReleaseInput input, MesStore store, HttpResponse response) => WriteMutation(store.Release(sn, input), response));
app.MapGet("/api/events", (int? limit, string? workOrderNo, MesStore store) => new { items = store.Events(limit ?? 100, workOrderNo) });
app.MapGet("/api/events/export.csv", (string? workOrderNo, MesStore store) => Results.File(System.Text.Encoding.UTF8.GetBytes(store.ExportCsv(workOrderNo)), "text/csv; charset=utf-8", "tracegate-mes-lab-events.csv"));
app.MapFallback("/api/{**path}", () => Results.Json(new { error = new { code = "not_found", message = "API endpoint not found." } }, statusCode: 404));
app.Run();

static IResult WriteMutation(MutationResult result, HttpResponse response)
{
    if (result.Replayed) response.Headers["Idempotent-Replayed"] = "true";
    return Results.Content(result.Json, "application/json", statusCode: result.StatusCode);
}
