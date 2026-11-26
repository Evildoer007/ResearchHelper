using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// Adapted from the read-only OH host. ResearchHelper owns its service and store.

namespace ResearchHelper.Windows;

internal sealed record BackendConnection(Process Process, Uri Address);

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        if (args.Contains("--check-webview2", StringComparer.Ordinal))
        {
            try
            {
                Console.WriteLine(CoreWebView2Environment.GetAvailableBrowserVersionString());
            }
            catch (Exception error)
            {
                Console.Error.WriteLine($"WebView2 Runtime不可用：{error.Message}");
                Environment.ExitCode = 1;
            }
            return;
        }
        ApplicationConfiguration.Initialize();
        var resources = Path.Combine(AppContext.BaseDirectory, "Resources");
        var backend = Path.Combine(resources, "backend", "researchhelper-server", "researchhelper-server.exe");
        if (!File.Exists(backend))
        {
            MessageBox.Show("找不到内置应用服务。", "ResearchHelper", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        var state = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ResearchHelper", "research-v1");
        Directory.CreateDirectory(state);
        BackendConnection? initial = null;
        MainForm? form = null;
        string? startupFailure = null;
        try
        {
            // The first startup runs before WinForms creates its UI context.
            initial = StartBackend(backend, state).GetAwaiter().GetResult();
            form = new MainForm(initial, state, port => StartBackend(backend, state, port));
            Application.Run(form);
        }
        catch (Exception error)
        {
            startupFailure = error is BackendStartupException ? error.Message : "请确认应用组件和研究档案可用，再重新打开ResearchHelper。";
        }
        finally
        {
            var owned = form?.Backend ?? initial;
            if (owned is not null) StopBackend(owned.Process);
            form?.Dispose();
        }
        if (startupFailure is not null)
            MessageBox.Show($"应用服务未能启动。\n{startupFailure}", "ResearchHelper", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    private static async Task<BackendConnection> StartBackend(string backend, string state, int? port = null)
    {
        var process = new Process
        {
            StartInfo = new ProcessStartInfo(backend)
            {
                WorkingDirectory = Path.GetDirectoryName(backend)!,
                UseShellExecute = false,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = System.Text.Encoding.UTF8,
                StandardErrorEncoding = System.Text.Encoding.UTF8,
                CreateNoWindow = true,
            }
        };
        var started = false;
        try
        {
            process.StartInfo.Environment["PYTHONUTF8"] = "1";
            process.StartInfo.Environment["PYTHONIOENCODING"] = "utf-8";
            process.StartInfo.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
            process.StartInfo.Environment["RESEARCHHELPER_STORE"] = state;
            process.StartInfo.Environment["RESEARCHHELPER_DESKTOP_LIFETIME"] = "stdin";
            process.StartInfo.Environment.Remove("RESEARCHHELPER_DESKTOP_PORT");
            if (port is int expectedPort)
                process.StartInfo.Environment["RESEARCHHELPER_DESKTOP_PORT"] = expectedPort.ToString(CultureInfo.InvariantCulture);
            process.Start();
            started = true;
            _ = DrainOutput(process.StandardError);
            var address = new Uri(await WaitForUrl(process, TimeSpan.FromSeconds(45)).ConfigureAwait(false));
            if (port is not null && address.Port != port)
                throw new BackendStartupException("本地研究服务未能建立预期连接，请重试。");
            _ = DrainOutput(process.StandardOutput);
            return new BackendConnection(process, address);
        }
        catch
        {
            if (started) StopBackend(process);
            else process.Dispose();
            throw;
        }
    }

    internal static void StopBackend(Process process)
    {
        using (process)
        {
            // The backend keeps its lease until admitted writes settle.
            try { process.StandardInput.Close(); }
            catch (IOException) { }
            process.WaitForExit(5000);
        }
    }

    private static async Task DrainOutput(StreamReader output)
    {
        try
        {
            await output.BaseStream.CopyToAsync(Stream.Null).ConfigureAwait(false);
        }
        catch (IOException) { }
        catch (ObjectDisposedException) { }
    }

    internal sealed class BackendStartupException : Exception
    {
        public BackendStartupException(string message) : base(message) { }
    }

    private static string StartupFailure(string? code) => code switch
    {
        "store_unavailable" => "研究档案无法打开。请确认档案目录可用，并关闭正在使用同一档案的ResearchHelper。",
        "execution_policy_unavailable" => "运行策略无法读取。请恢复有效的execution-policy.json后重新打开ResearchHelper。",
        "model_configuration_unavailable" => "模型配置无法读取。请恢复有效的model-settings.json后重新打开ResearchHelper。",
        "source_configuration_unavailable" => "数据源配置无法读取。请恢复有效的source-settings.json后重新打开ResearchHelper。",
        "account_unavailable" => "本地账户无法读取。请恢复有效的local-account.json后重新打开ResearchHelper。",
        "sessions_unavailable" => "本地会话无法读取。请恢复有效的local-sessions.json后重新打开ResearchHelper。",
        "local_listener_unavailable" => "本地连接端口暂不可用，请稍后重试。",
        _ => "本地服务未返回有效启动状态。"
    };

    private static async Task<string> WaitForUrl(Process process, TimeSpan timeout)
    {
        using var cancellation = new CancellationTokenSource(timeout);
        string? line;
        try
        {
            // A rejected handshake may already be buffered when the process exits.
            line = await process.StandardOutput.ReadLineAsync(cancellation.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            throw new BackendStartupException("本地研究服务启动超时，请重新打开ResearchHelper。");
        }
        if (string.IsNullOrEmpty(line) || line.Length >= 8192)
            throw new BackendStartupException(StartupFailure(null));
        try
        {
            using var message = JsonDocument.Parse(line);
            var root = message.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("ready", out var ready))
                throw new BackendStartupException(StartupFailure(null));
            var hasCode = root.TryGetProperty("failure_code", out var failure);
            var hasAddress = root.TryGetProperty("url", out var address);
            if (ready.ValueKind == JsonValueKind.False && !hasAddress && hasCode && failure.ValueKind == JsonValueKind.String)
                throw new BackendStartupException(StartupFailure(failure.GetString()));
            if (ready.ValueKind == JsonValueKind.True && !hasCode && hasAddress && address.ValueKind == JsonValueKind.String
                && Uri.TryCreate(address.GetString(), UriKind.Absolute, out var url)
                && url.Scheme == "http" && url.Host == "127.0.0.1" && url.Port > 0
                && string.IsNullOrEmpty(url.UserInfo) && url.AbsolutePath == "/"
                && string.IsNullOrEmpty(url.Query) && string.IsNullOrEmpty(url.Fragment))
            {
                if (process.HasExited)
                    throw new BackendStartupException("本地研究服务在启动时退出，请重新打开ResearchHelper。");
                return url.AbsoluteUri;
            }
        }
        catch (JsonException)
        {
            // The native message is fixed and independent of backend output.
        }
        throw new BackendStartupException(StartupFailure(null));
    }
}

internal sealed class ReportNavigation(Uri expected)
{
    private ulong? requestId;
    internal sealed record Receipt(string? Example, string Profile, string? Digest);

    internal static bool IsReport(Uri origin, Uri url)
        => url.IsAbsoluteUri && url.Scheme == origin.Scheme && url.Host == origin.Host && url.Port == origin.Port
            && string.IsNullOrEmpty(url.UserInfo) && string.IsNullOrEmpty(url.Fragment)
            && url.Query is "" or "?profile=standard" or "?profile=detailed"
            && (Regex.IsMatch(url.AbsolutePath, @"\A/api/tasks/[A-Za-z0-9_-]+/report/[1-9][0-9]*\z")
                || Regex.IsMatch(url.AbsolutePath, @"\A/reports/(?:semi|ipo|broker|consumer|medicine|internet)\.html\z"));

    internal bool Matches(string source)
        => Uri.TryCreate(source, UriKind.Absolute, out var url) && string.IsNullOrEmpty(url.UserInfo)
            && Uri.Compare(url, expected, UriComponents.SchemeAndServer | UriComponents.PathAndQuery,
                UriFormat.UriEscaped, StringComparison.Ordinal) == 0;

    internal bool Start(string source, ulong navigationId)
    {
        if (requestId is null && source == "about:blank") return true;
        if (!Matches(source) || requestId is not null && requestId != navigationId) return false;
        requestId = navigationId;
        return true;
    }

    internal bool Owns(ulong navigationId) => requestId == navigationId;

    internal bool CanShow(string source, bool success, int status)
        => success && status is >= 200 and < 300 && Matches(source);

    internal Receipt? ReadResponse(string source, int status, string? contentType,
        string? identity, string? expectedDigest)
    {
        if (!CanShow(source, true, status)) return null;
        var mime = contentType?.Split(';', 2)[0].Trim();
        if (!string.Equals(mime, "text/html", StringComparison.OrdinalIgnoreCase)
            && !string.Equals(mime, "application/xhtml+xml", StringComparison.OrdinalIgnoreCase)) return null;
        var profile = expected.Query == "?profile=detailed" ? "detailed" : "standard";
        if (expected.AbsolutePath.StartsWith("/reports/", StringComparison.Ordinal))
            return new Receipt(Path.GetFileNameWithoutExtension(expected.AbsolutePath), profile, null);
        var path = expected.AbsolutePath.Split('/', StringSplitOptions.RemoveEmptyEntries);
        var fields = identity?.Split(':');
        if (path.Length != 5 || fields is not { Length: 3 } || fields[0] != path[2] || fields[1] != path[4]
            || !Regex.IsMatch(fields[2], @"\A[a-f0-9]{64}\z")
            || expectedDigest is not null && fields[2] != expectedDigest) return null;
        return new Receipt(null, profile, fields[2]);
    }
}

internal sealed class MainForm : Form
{
    private const int WmSysCommand = 0x0112;
    private const int ScaleUpCommand = 0x1F10;
    private const int ScaleDownCommand = 0x1F20;
    private const int ScaleResetCommand = 0x1F30;
    private const uint MfString = 0x0000;
    private const uint MfSeparator = 0x0800;
    private const uint MfByCommand = 0x0000;
    private const uint MfEnabled = 0x0000;
    private const uint MfGrayed = 0x0001;
    private const int DwmwaUseImmersiveDarkMode = 20;
    private const int DwmwaSystemBackdropType = 38;
    private const int DwmsbtMainWindow = 2;

    private static readonly double[] ScaleSteps = [0.8, 0.9, 1.0, 1.1, 1.25, 1.4];
    private readonly WebView2 browser = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.Transparent };
    private readonly string uiPreferencesPath;
    private readonly Uri appOrigin;
    private readonly Func<int, Task<BackendConnection>> startReplacement;
    private readonly TableLayoutPanel recoveryPanel = new() { Dock = DockStyle.Fill, Visible = false, ColumnCount = 1, RowCount = 5 };
    private readonly Label recoveryTitle = new() { AutoSize = true, Anchor = AnchorStyles.None, Text = "本地研究服务已中断" };
    private readonly Label recoveryDetail = new() { AutoSize = true, Anchor = AnchorStyles.None, Margin = new Padding(8, 8, 8, 12) };
    private readonly Button reconnect = new() { AutoSize = true, Anchor = AnchorStyles.None, Padding = new Padding(10, 4, 10, 4), Text = "重新连接" };
    private bool backendUnavailable;
    internal BackendConnection Backend { get; private set; }
    private readonly HashSet<Form> reportWindows = [];
    private readonly SemaphoreSlim uiScaleScriptLock = new(1, 1);
    private double uiScale;
    private bool webViewReady;
    private bool reportWorkspaceReady;
    private bool systemMenuInstalled;
    private bool nativeBackdropEnabled;
    private string surfaceTheme = "light";
    private string? uiScaleScriptId;
    private CancellationTokenSource? activeReportPrint;
    private long workspaceGeneration;
    private bool artifactSaveInProgress;
    private bool closingAfterDraft;
    private bool waitingForDraft;

    internal MainForm(BackendConnection backend, string stateDirectory, Func<int, Task<BackendConnection>> startReplacement)
    {
        Backend = backend;
        appOrigin = backend.Address;
        this.startReplacement = startReplacement;
        Text = "ResearchHelper";
        Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        AutoScaleDimensions = new SizeF(96, 96);
        AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(1320, 860);
        StartPosition = FormStartPosition.Manual;
        KeyPreview = true;
        BackColor = Color.FromArgb(247, 247, 247);
        uiPreferencesPath = Path.Combine(stateDirectory, "ui-preferences.json");
        uiScale = UIScalePreferences.Load(uiPreferencesPath, ScaleSteps);
        Controls.Add(browser);
        recoveryPanel.BackColor = BackColor;
        recoveryTitle.Font = new Font(Font.FontFamily, 17, FontStyle.Bold);
        recoveryPanel.RowStyles.Add(new RowStyle(SizeType.Percent, 50));
        for (var row = 0; row < 3; row++) recoveryPanel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        recoveryPanel.RowStyles.Add(new RowStyle(SizeType.Percent, 50));
        recoveryPanel.Controls.Add(recoveryTitle, 0, 1);
        recoveryPanel.Controls.Add(recoveryDetail, 0, 2);
        recoveryPanel.Controls.Add(reconnect, 0, 3);
        reconnect.Click += async (_, _) => await ReconnectBackend();
        Controls.Add(recoveryPanel);
        Shown += async (_, _) =>
        {
            WatchBackend(backend);
            try
            {
                var webViewData = Path.Combine(stateDirectory, "webview2");
                Directory.CreateDirectory(webViewData);
                var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: webViewData);
                await browser.EnsureCoreWebView2Async(environment);
                browser.CoreWebView2.Settings.IsZoomControlEnabled = false;
                browser.ZoomFactor = uiScale;
                await UpdateScaleBootstrapScript();
                browser.CoreWebView2.WebMessageReceived += HandleWebMessage;
                browser.CoreWebView2.NewWindowRequested += OpenReportWindow;
                browser.CoreWebView2.NavigationStarting += (_, navigation) =>
                {
                    navigation.Cancel = !Uri.TryCreate(navigation.Uri, UriKind.Absolute, out var target) || backendUnavailable
                        || !(IsSameOrigin(target) || target.AbsoluteUri == "about:blank");
                    if (!navigation.Cancel)
                    {
                        reportWorkspaceReady = false;
                        workspaceGeneration++;
                        activeReportPrint?.Cancel();
                        if (target?.AbsolutePath == "/login/index.html") CloseReportWindows();
                    }
                };
                browser.CoreWebView2.NavigationCompleted += (_, _) => SyncScaleToPage();
                webViewReady = true;
                if (!backendUnavailable)
                {
                    var startupUrl = new UriBuilder(appOrigin) { Query = "app_startup=" + Guid.NewGuid().ToString("N") };
                    browser.CoreWebView2.Navigate(startupUrl.Uri.AbsoluteUri);
                }
            }
            catch (Exception error)
            {
                MessageBox.Show(
                    $"WebView2 Runtime不可用，ResearchHelper无法显示界面。\n请安装Microsoft Edge WebView2 Runtime后重试。\n\n{error.Message}",
                    "ResearchHelper",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                Close();
            }
        };
    }

    private void WatchBackend(BackendConnection observed)
    {
        void ReportExit()
        {
            try
            {
                if (IsHandleCreated && !IsDisposed)
                    BeginInvoke((Action)(() => _ = BackendDisconnected(observed)));
            }
            catch (InvalidOperationException) { }
        }
        observed.Process.Exited += (_, _) => ReportExit();
        observed.Process.EnableRaisingEvents = true;
        if (observed.Process.HasExited) ReportExit();
    }

    private async Task BackendDisconnected(BackendConnection observed)
    {
        if (backendUnavailable || !ReferenceEquals(Backend, observed) || IsDisposed || Disposing) return;
        backendUnavailable = true;
        LeaveWorkspace();
        recoveryTitle.Text = "本地研究服务已中断";
        recoveryDetail.Text = "重新连接后可继续查看已保存的研究。";
        reconnect.Enabled = false;
        recoveryPanel.BringToFront();
        recoveryPanel.Visible = true;
        try
        {
            if (webViewReady && browser.Source is Uri source && IsSameOrigin(source) && source.AbsolutePath == "/")
                await browser.CoreWebView2.ExecuteScriptAsync("window.ResearchHelperNative?.prepareServiceRecovery?.()")
                    .WaitAsync(TimeSpan.FromSeconds(1));
        }
        catch (Exception error) when (error is InvalidOperationException or TimeoutException or COMException) { }
        if (IsDisposed || Disposing) return;
        reconnect.Enabled = true;
        reconnect.Focus();
    }

    private async Task ReconnectBackend()
    {
        if (!backendUnavailable || !reconnect.Enabled || IsDisposed || Disposing) return;
        reconnect.Enabled = false;
        recoveryTitle.Text = "正在连接本地研究服务";
        recoveryDetail.Text = "";
        BackendConnection replacement;
        try
        {
            replacement = await startReplacement(appOrigin.Port);
        }
        catch (Exception error)
        {
            if (IsDisposed || Disposing) return;
            recoveryTitle.Text = "暂时无法连接";
            recoveryDetail.Text = error is Program.BackendStartupException ? error.Message : "请稍后重试。";
            reconnect.Enabled = true;
            reconnect.Focus();
            return;
        }
        if (IsDisposed || Disposing)
        {
            Program.StopBackend(replacement.Process);
            return;
        }
        var previous = Backend;
        Backend = replacement;
        Program.StopBackend(previous.Process);
        backendUnavailable = false;
        WatchBackend(replacement);
        recoveryPanel.Visible = false;
        if (webViewReady) browser.CoreWebView2.Navigate(replacement.Address.AbsoluteUri);
    }

    protected override void OnLoad(EventArgs eventArgs)
    {
        base.OnLoad(eventArgs);
        // The handle now has the selected monitor's DPI. Match the macOS
        // logical content size, then use a large, centered working-area fit.
        // This runs only on first display; later user resizing stays untouched.
        var workArea = Screen.FromHandle(Handle).WorkingArea;
        var scale = DeviceDpi / 96.0;
        var preferred = SizeFromClientSize(new Size(
            (int)Math.Round(1320 * scale), (int)Math.Round(860 * scale)));
        Bounds = InitialWindowBounds(workArea, preferred, DeviceDpi);
    }

    internal static Rectangle InitialWindowBounds(Rectangle workArea, Size preferred, int dpi)
    {
        var margin = Math.Clamp((int)Math.Round(24 * dpi / 96.0), 0,
            Math.Min(workArea.Width, workArea.Height) / 4);
        var width = Math.Min(Math.Max(1, workArea.Width - 2 * margin),
            Math.Max(preferred.Width, (int)Math.Round(workArea.Width * 0.9)));
        var height = Math.Min(Math.Max(1, workArea.Height - 2 * margin),
            Math.Max(preferred.Height, (int)Math.Round(workArea.Height * 0.9)));
        return new Rectangle(workArea.Left + (workArea.Width - width) / 2,
            workArea.Top + (workArea.Height - height) / 2, width, height);
    }

    private bool IsSameOrigin(Uri url) => url.Scheme == appOrigin.Scheme
        && url.Host == appOrigin.Host && url.Port == appOrigin.Port && string.IsNullOrEmpty(url.UserInfo);

    private async void OpenReportWindow(object? sender, CoreWebView2NewWindowRequestedEventArgs eventArgs)
    {
        eventArgs.Handled = true;
        if (!CanOpenReport() || sender != browser.CoreWebView2 ||
            !Uri.TryCreate(eventArgs.Uri, UriKind.Absolute, out var url) || !ReportNavigation.IsReport(appOrigin, url)) return;
        var generation = workspaceGeneration;
        try
        {
            await ShowReportWindow(url, print: null, CancellationToken.None);
        }
        catch (Exception)
        {
            if (generation == workspaceGeneration && CanOpenReport())
                MessageBox.Show(this, "报告未能打开，请检查登录状态和报告版本后重试。",
                    "ResearchHelper", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    protected override void OnHandleCreated(EventArgs eventArgs)
    {
        base.OnHandleCreated(eventArgs);
        nativeBackdropEnabled = TryEnableSystemBackdrop();
        ApplySurfaceTheme(surfaceTheme);
        if (systemMenuInstalled) return;
        var menu = GetSystemMenu(Handle, false);
        if (menu == IntPtr.Zero) return;
        AppendMenu(menu, MfSeparator, UIntPtr.Zero, string.Empty);
        AppendMenu(menu, MfString, new UIntPtr((uint)ScaleUpCommand), "放大\tCtrl++");
        AppendMenu(menu, MfString, new UIntPtr((uint)ScaleDownCommand), "缩小\tCtrl+-");
        AppendMenu(menu, MfString, new UIntPtr((uint)ScaleResetCommand), "实际大小\tCtrl+0");
        systemMenuInstalled = true;
        UpdateSystemMenuAvailability();
    }

    protected override void OnHandleDestroyed(EventArgs eventArgs)
    {
        systemMenuInstalled = false;
        nativeBackdropEnabled = false;
        base.OnHandleDestroyed(eventArgs);
    }

    protected override void OnPaintBackground(PaintEventArgs eventArgs)
    {
        if (!nativeBackdropEnabled) base.OnPaintBackground(eventArgs);
    }

    protected override bool ProcessCmdKey(ref Message message, Keys keyData)
    {
        if ((keyData & Keys.Control) == Keys.Control)
        {
            var keyCode = keyData & Keys.KeyCode;
            if (keyCode is Keys.Oemplus or Keys.Add)
            {
                ChangeScale(1);
                return true;
            }
            if (keyCode is Keys.OemMinus or Keys.Subtract)
            {
                ChangeScale(-1);
                return true;
            }
            if (keyCode is Keys.D0 or Keys.NumPad0)
            {
                ApplyScale(1.0, persist: true, notifyPage: true);
                return true;
            }
            if (keyCode == Keys.P)
            {
                _ = PrintCurrentReport();
                return true;
            }
        }
        return base.ProcessCmdKey(ref message, keyData);
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == WmSysCommand)
        {
            switch (message.WParam.ToInt32() & 0xFFF0)
            {
                case ScaleUpCommand:
                    ChangeScale(1);
                    return;
                case ScaleDownCommand:
                    ChangeScale(-1);
                    return;
                case ScaleResetCommand:
                    ApplyScale(1.0, persist: true, notifyPage: true);
                    return;
            }
        }
        base.WndProc(ref message);
    }

    private void HandleWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs eventArgs)
    {
        if (backendUnavailable || !Uri.TryCreate(eventArgs.Source, UriKind.Absolute, out var source)
            || !IsSameOrigin(source) || source.AbsolutePath != "/" && source.AbsolutePath != "/login/index.html") return;
        try
        {
            using var document = JsonDocument.Parse(eventArgs.WebMessageAsJson);
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("type", out var type)) return;
            // Login may synchronize its theme, but cannot invoke file/print actions.
            if (source.AbsolutePath != "/" && type.GetString() != "set_theme") return;
            if (type.GetString() == "researchhelper_action" && root.TryGetProperty("action", out var action))
            {
                var requestedAction = action.GetString();
                if (requestedAction == "signed_out") LeaveWorkspace();
                else if (requestedAction == "workspace-state" && root.TryGetProperty("ready", out var ready)
                    && ready.ValueKind is JsonValueKind.True or JsonValueKind.False)
                    reportWorkspaceReady = ready.GetBoolean();
                else if (requestedAction == "print") _ = PrintCurrentReport();
                else if (requestedAction is "save" or "save_binary" or "save_original" or "save_backup")
                    SaveArtifact(root, binary: requestedAction != "save", original: requestedAction == "save_original",
                        backup: requestedAction == "save_backup");
                else if (requestedAction == "zoom" && root.TryGetProperty("scale", out var zoom)
                    && zoom.TryGetDouble(out var requestedZoom) && NormalizeScale(requestedZoom) is double acceptedZoom)
                    ApplyScale(acceptedZoom, persist: true, notifyPage: true);
                return;
            }
            if (type.GetString() == "signed_out")
            {
                LeaveWorkspace();
                return;
            }
            if (type.GetString() == "set_theme"
                && root.TryGetProperty("theme", out var theme)
                && theme.GetString() is string requestedTheme
                && requestedTheme is "light" or "dark")
            {
                ApplySurfaceTheme(requestedTheme);
                return;
            }
            if (type.GetString() != "set_ui_scale"
                || !root.TryGetProperty("scale", out var scale)
                || !scale.TryGetDouble(out var requested)
                || NormalizeScale(requested) is not double accepted) return;
            ApplyScale(accepted, persist: true, notifyPage: true);
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException)
        {
            // Ignore malformed messages from page scripts.
        }
    }

    protected override void OnFormClosing(FormClosingEventArgs eventArgs)
    {
        base.OnFormClosing(eventArgs);
        if (eventArgs.Cancel) return;
        if (eventArgs.CloseReason == CloseReason.UserClosing && !closingAfterDraft && WorkspaceIsActive())
        {
            eventArgs.Cancel = true;
            if (!waitingForDraft)
            {
                waitingForDraft = true;
                _ = FlushDraftThenClose();
            }
            return;
        }
        LeaveWorkspace();
    }

    private async Task FlushDraftThenClose()
    {
        // Let the first FormClosing finish before a completed save can close it again.
        await Task.Yield();
        try
        {
            if (WorkspaceIsActive())
            {
                var request = JsonSerializer.Serialize(new
                {
                    expression = "location.pathname === '/' && window.ResearchHelperNative?.flushWorkspaceDraft?.()",
                    awaitPromise = true,
                });
                await browser.CoreWebView2.CallDevToolsProtocolMethodAsync("Runtime.evaluate", request)
                    .WaitAsync(TimeSpan.FromMilliseconds(1500));
            }
        }
        catch (Exception error) when (error is TimeoutException or InvalidOperationException or COMException)
        {
            // Close normally if the local browser cannot finish the best-effort flush.
        }
        finally
        {
            waitingForDraft = false;
            closingAfterDraft = true;
            if (!IsDisposed && !Disposing) Close();
        }
    }

    private void LeaveWorkspace()
    {
        reportWorkspaceReady = false;
        workspaceGeneration++;
        CloseReportWindows();
    }

    private void CloseReportWindows()
    {
        activeReportPrint?.Cancel();
        foreach (var reportWindow in reportWindows.ToArray()) reportWindow.Close();
    }

    private void SaveArtifact(JsonElement message, bool binary, bool original, bool backup)
    {
        if (!WorkspaceIsActive() || ArtifactSave.ReadString(message, "operation_id") is not { Length: > 0 and <= 128 } operationId
            || operationId.Any(char.IsControl)) return;
        var generation = workspaceGeneration;
        if (artifactSaveInProgress)
        {
            ReplyArtifactSave(generation, operationId, "rejected", "研究文件", "请先完成或取消当前保存窗口");
            return;
        }
        if (ArtifactSave.ReadContent(message, binary, original, backup) is not { } file)
        {
            ReplyArtifactSave(generation, operationId, "rejected", "研究文件", "文件内容或格式无效，请重新导出");
            return;
        }
        artifactSaveInProgress = true;
        try
        {
            // A modal message loop must start after WebView2's callback returns.
            BeginInvoke((Action)(() => SaveArtifactToFile(file, operationId, generation)));
        }
        catch (InvalidOperationException)
        {
            artifactSaveInProgress = false;
            ReplyArtifactSave(generation, operationId, "failed", file.Name, "无法打开保存窗口，请重新导出");
        }
    }

    private void SaveArtifactToFile((string Name, byte[] Data) file, string operationId, long generation)
    {
        var status = "cancelled";
        var name = file.Name;
        string? error = null;
        try
        {
            if (!OwnsArtifactSave(generation)) return;
            var extension = Path.GetExtension(name);
            using var dialog = new SaveFileDialog
            {
                FileName = name, Filter = "研究文件|*" + extension, DefaultExt = extension.TrimStart('.'),
            };
            if (dialog.ShowDialog(this) == DialogResult.OK)
            {
                if (!OwnsArtifactSave(generation)) return;
                ArtifactSave.Write(dialog.FileName, file.Data);
                name = Path.GetFileName(dialog.FileName);
                status = "saved";
            }
        }
        catch (Exception failure) when (failure is IOException or UnauthorizedAccessException
            or InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            status = "failed";
            error = "文件未保存，请选择可写目录。报告仍保留在研究档案中。";
        }
        finally
        {
            artifactSaveInProgress = false;
        }
        ReplyArtifactSave(generation, operationId, status, name, error);
    }

    private bool OwnsArtifactSave(long generation) => generation == workspaceGeneration && WorkspaceIsActive();

    private void ReplyArtifactSave(long generation, string operationId, string status, string name, string? error)
    {
        if (!OwnsArtifactSave(generation)) return;
        var reply = JsonSerializer.Serialize(new { type = "researchhelper_artifact_saved", operation_id = operationId, status, name, error });
        browser.CoreWebView2.PostWebMessageAsJson(reply);
    }

    private const string CurrentReportPrintRequest = "window.ResearchHelperNative?.printRequest?.() ?? null";
    private const string ReportPreviewHeader = "X-ResearchHelper-Report-Preview";
    private const string ReportIdentityHeader = "X-ResearchHelper-Report";
    private const string PrepareReportDocument = """
        (example, variant, digest, printing, presentation) => {
            if (document.readyState !== 'complete' ||
                !['text/html', 'application/xhtml+xml'].includes(document.contentType) ||
                !document.querySelector('main') || !document.querySelector('h1')) return false;
            if (example === null) {
                let paper;
                try { paper = JSON.parse(document.querySelector('script#rh-publication-document[type="application/json"]')?.textContent); }
                catch { return false; }
                if (paper?.schema !== 'researchhelper.presentation-document' || paper.version !== 1 ||
                    paper.profile !== variant || document.body?.dataset.reportVariant !== variant ||
                    digest === null || paper.content_hash !== digest) return false;
            } else if (document.querySelector('meta[name="rh-source-key"]')?.content !== example) return false;
            if (presentation !== null) {
                if (typeof window.RHVisuals?.applyProfile !== 'function') return false;
                try { window.RHVisuals.applyProfile(presentation); }
                catch { return false; }
            }
            if (printing) window.dispatchEvent(new Event('beforeprint'));
            return true;
        }
        """;

    private bool WorkspaceIsActive() => webViewReady && !backendUnavailable && !IsDisposed && !Disposing
        && browser.Source is Uri source && IsSameOrigin(source) && source.AbsolutePath == "/";

    private bool CanOpenReport() => reportWorkspaceReady && WorkspaceIsActive();
    private sealed record ReportPrintOptions(string? Digest, string Presentation);

    private async Task PrintCurrentReport()
    {
        if (!CanOpenReport() || activeReportPrint is not null) return;
        using var request = new CancellationTokenSource();
        activeReportPrint = request;
        try
        {
            var raw = await browser.CoreWebView2.ExecuteScriptAsync(CurrentReportPrintRequest).WaitAsync(TimeSpan.FromSeconds(10), request.Token);
            request.Token.ThrowIfCancellationRequested();
            if (!CanOpenReport()) return;
            using var response = JsonDocument.Parse(raw);
            var selected = response.RootElement;
            if (selected.ValueKind != JsonValueKind.Object || !selected.TryGetProperty("url", out var address)
                || address.ValueKind != JsonValueKind.String
                || !Uri.TryCreate(address.GetString(), UriKind.Absolute, out var url) || !ReportNavigation.IsReport(appOrigin, url))
            {
                MessageBox.Show(this, "请先打开一份研究报告，再打印或保存为PDF。", "ResearchHelper");
                return;
            }
            var digest = selected.TryGetProperty("digest", out var identity) && identity.ValueKind == JsonValueKind.String
                ? identity.GetString() : null;
            if (!url.AbsolutePath.StartsWith("/reports/", StringComparison.Ordinal) &&
                (digest is null || !Regex.IsMatch(digest, @"\A[a-f0-9]{64}\z"))) throw new InvalidOperationException();
            var presentation = "null";
            if (selected.TryGetProperty("presentation", out var profile) && profile.ValueKind != JsonValueKind.Null)
            {
                if (profile.ValueKind != JsonValueKind.Object) throw new InvalidOperationException();
                presentation = JsonSerializer.Serialize(profile);
            }
            await ShowReportWindow(url, new ReportPrintOptions(digest, presentation), request.Token);
        }
        catch (OperationCanceledException) when (request.IsCancellationRequested) { }
        catch (Exception)
        {
            if (CanOpenReport() && !request.IsCancellationRequested)
                MessageBox.Show(this, "报告未能准备打印，请重新打开后再试。", "ResearchHelper");
        }
        finally { activeReportPrint = null; }
    }

    private async Task ShowReportWindow(Uri url, ReportPrintOptions? print, CancellationToken cancellation)
    {
        var child = new Form { Text = "研究报告", Width = 1000, Height = 800 };
        var view = new WebView2 { Dock = DockStyle.Fill, Visible = false };
        using var loading = new Label { Dock = DockStyle.Fill, Text = "正在读取报告…", TextAlign = ContentAlignment.MiddleCenter };
        child.Controls.Add(view);
        child.Controls.Add(loading);
        reportWindows.Add(child);
        child.FormClosed += (_, _) => reportWindows.Remove(child);
        using var preview = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        FormClosedEventHandler stopPreview = (_, _) => preview.Cancel();
        child.FormClosed += stopPreview;
        try
        {
            child.Show(this);
            // Create the child handle while the loading surface is visible.
            // The WebView remains hidden until the exact response is verified.
            view.Bounds = child.ClientRectangle;
            _ = view.Handle;
            await view.EnsureCoreWebView2Async(browser.CoreWebView2.Environment).WaitAsync(TimeSpan.FromSeconds(15), preview.Token);
            preview.Token.ThrowIfCancellationRequested();
            view.ZoomFactor = uiScale;
            var core = view.CoreWebView2;
            core.Settings.IsWebMessageEnabled = false;
            core.NewWindowRequested += (_, request) => request.Handled = true;

            var loaded = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var receipt = new TaskCompletionSource<ReportNavigation.Receipt?>(TaskCreationOptions.RunContinuationsAsynchronously);
            var document = new ReportNavigation(url);
            core.NavigationStarting += (_, navigation) =>
            {
                navigation.Cancel = !document.Start(navigation.Uri, navigation.NavigationId);
                if (navigation.Cancel) loaded.TrySetResult(false);
            };
            void CompleteLoad(object? sender, CoreWebView2NavigationCompletedEventArgs navigation)
            {
                if (document.Owns(navigation.NavigationId))
                    loaded.TrySetResult(document.CanShow(core.Source, navigation.IsSuccess, navigation.HttpStatusCode));
            }
            void ReadResponse(object? sender, CoreWebView2WebResourceResponseReceivedEventArgs received)
            {
                var request = received.Request;
                if (request.Method != "GET" || !document.Matches(request.Uri)
                    || !request.Headers.Contains(ReportPreviewHeader)
                    || request.Headers.GetHeader(ReportPreviewHeader) != "1") return;
                var response = received.Response;
                var headers = response.Headers;
                receipt.TrySetResult(document.ReadResponse(request.Uri, response.StatusCode,
                    headers.Contains("Content-Type") ? headers.GetHeader("Content-Type") : null,
                    headers.Contains(ReportIdentityHeader) ? headers.GetHeader(ReportIdentityHeader) : null,
                    print?.Digest));
            }
            core.NavigationCompleted += CompleteLoad;
            core.WebResourceResponseReceived += ReadResponse;
            ReportNavigation.Receipt identity;
            try
            {
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(preview.Token);
                deadline.CancelAfter(TimeSpan.FromSeconds(20));
                var request = core.Environment.CreateWebResourceRequest(url.AbsoluteUri, "GET", null, ReportPreviewHeader + ": 1");
                core.NavigateWithWebResourceRequest(request);
                // The response event can arrive before or after navigation completion.
                if (!await loaded.Task.WaitAsync(deadline.Token))
                    throw new InvalidOperationException();
                identity = await receipt.Task.WaitAsync(deadline.Token) ?? throw new InvalidOperationException();
            }
            finally
            {
                if (!child.IsDisposed)
                {
                    core.NavigationCompleted -= CompleteLoad;
                    core.WebResourceResponseReceived -= ReadResponse;
                }
            }

            var arguments = $"{JsonSerializer.Serialize(identity.Example)}, {JsonSerializer.Serialize(identity.Profile)}, {JsonSerializer.Serialize(identity.Digest)}";
            var prepare = $"({PrepareReportDocument})({arguments}, {JsonSerializer.Serialize(print is not null)}, {print?.Presentation ?? "null"})";
            var prepared = await core.ExecuteScriptAsync(prepare).WaitAsync(TimeSpan.FromSeconds(10), preview.Token);
            preview.Token.ThrowIfCancellationRequested();
            if (prepared != "true" || !document.Matches(core.Source)) throw new InvalidOperationException();
            loading.Hide();
            view.Show();
            if (print is not null)
            {
                // Let print styles and chart resizing settle before handing off to Windows.
                await Task.Delay(TimeSpan.FromMilliseconds(300), preview.Token);
                if (!document.Matches(core.Source)) throw new InvalidOperationException();
                core.ShowPrintUI(CoreWebView2PrintDialogKind.System);
            }
        }
        catch (OperationCanceledException) when (preview.IsCancellationRequested)
        {
            if (!child.IsDisposed) child.Close();
        }
        catch
        {
            if (!child.IsDisposed) child.Close();
            throw;
        }
        finally
        {
            if (!child.IsDisposed) child.FormClosed -= stopPreview;
        }
    }

    private static double? NormalizeScale(double value)
    {
        foreach (var step in ScaleSteps)
        {
            if (Math.Abs(step - value) < 0.0001) return step;
        }
        return null;
    }

    private static string JavascriptNumber(double value) => value.ToString("0.####", CultureInfo.InvariantCulture);

    private void ChangeScale(int offset)
    {
        var current = Array.FindIndex(ScaleSteps, step => Math.Abs(step - uiScale) < 0.0001);
        if (current < 0) current = Array.IndexOf(ScaleSteps, 1.0);
        var next = Math.Clamp(current + offset, 0, ScaleSteps.Length - 1);
        ApplyScale(ScaleSteps[next], persist: true, notifyPage: true);
    }

    private void ApplyScale(double scale, bool persist, bool notifyPage)
    {
        var accepted = NormalizeScale(scale);
        if (accepted is null) return;
        uiScale = accepted.Value;
        if (webViewReady) browser.ZoomFactor = uiScale;
        if (persist) UIScalePreferences.Save(uiPreferencesPath, uiScale);
        UpdateSystemMenuAvailability();
        if (!webViewReady) return;
        _ = UpdateScaleBootstrapScript();
        if (notifyPage) SyncScaleToPage();
    }

    private async Task UpdateScaleBootstrapScript()
    {
        await uiScaleScriptLock.WaitAsync();
        try
        {
            if (browser.CoreWebView2 is null) return;
            if (!string.IsNullOrEmpty(uiScaleScriptId)) browser.CoreWebView2.RemoveScriptToExecuteOnDocumentCreated(uiScaleScriptId);
            var scale = JavascriptNumber(uiScale);
            var material = nativeBackdropEnabled ? "system" : "fallback";
            uiScaleScriptId = await browser.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                $"(()=>{{if(window.top!==window||!['/','/login/index.html'].includes(location.pathname))return;window.webkit??={{}};window.webkit.messageHandlers??={{}};window.webkit.messageHandlers.researchhelper={{postMessage:value=>window.chrome.webview.postMessage({{...value,type:'researchhelper_action'}})}};const apply=()=>{{if(!document.documentElement)return;document.documentElement.dataset.nativeShell='windows';document.documentElement.dataset.nativeMaterial='{material}';document.documentElement.dataset.uiScale='{scale}';if(location.pathname==='/'&&new URL(location.href).searchParams.has('app_startup'))document.documentElement.classList.add('login-boot');}};if(document.documentElement)apply();else document.addEventListener('DOMContentLoaded',apply,{{once:true}});}})();"
            );
        }
        finally
        {
            uiScaleScriptLock.Release();
        }
    }

    private void SyncScaleToPage()
    {
        if (!webViewReady) return;
        _ = browser.CoreWebView2.ExecuteScriptAsync($"document.documentElement.dataset.uiScale='{JavascriptNumber(uiScale)}';document.documentElement.style.removeProperty('zoom');window.dispatchEvent(new Event('resize'));");
    }

    private void UpdateSystemMenuAvailability()
    {
        if (!IsHandleCreated) return;
        var menu = GetSystemMenu(Handle, false);
        if (menu == IntPtr.Zero) return;
        EnableMenuItem(menu, ScaleDownCommand, MfByCommand | (uiScale > ScaleSteps[0] ? MfEnabled : MfGrayed));
        EnableMenuItem(menu, ScaleUpCommand, MfByCommand | (uiScale < ScaleSteps[^1] ? MfEnabled : MfGrayed));
        DrawMenuBar(Handle);
    }

    private bool TryEnableSystemBackdrop()
    {
        if (!OperatingSystem.IsWindowsVersionAtLeast(10, 0, 22621)) return false;
        try
        {
            var backdrop = DwmsbtMainWindow;
            if (DwmSetWindowAttribute(Handle, DwmwaSystemBackdropType, ref backdrop, sizeof(int)) != 0) return false;
            var margins = new Margins(-1);
            return DwmExtendFrameIntoClientArea(Handle, ref margins) == 0;
        }
        catch (DllNotFoundException)
        {
            return false;
        }
        catch (EntryPointNotFoundException)
        {
            return false;
        }
    }

    private void ApplySurfaceTheme(string theme)
    {
        surfaceTheme = theme == "dark" ? "dark" : "light";
        BackColor = surfaceTheme == "dark" ? Color.FromArgb(31, 31, 31) : Color.FromArgb(247, 247, 247);
        recoveryPanel.BackColor = BackColor;
        recoveryTitle.ForeColor = recoveryDetail.ForeColor = surfaceTheme == "dark" ? Color.WhiteSmoke : Color.FromArgb(38, 38, 38);
        if (IsHandleCreated && OperatingSystem.IsWindowsVersionAtLeast(10, 0, 17763))
        {
            var dark = surfaceTheme == "dark" ? 1 : 0;
            _ = DwmSetWindowAttribute(Handle, DwmwaUseImmersiveDarkMode, ref dark, sizeof(int));
        }
        Invalidate();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Margins
    {
        internal int Left;
        internal int Right;
        internal int Top;
        internal int Bottom;

        internal Margins(int value)
        {
            Left = value;
            Right = value;
            Top = value;
            Bottom = value;
        }
    }

    [DllImport("user32.dll")]
    private static extern IntPtr GetSystemMenu(IntPtr window, bool revert);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool AppendMenu(IntPtr menu, uint flags, UIntPtr item, string text);

    [DllImport("user32.dll")]
    private static extern bool EnableMenuItem(IntPtr menu, int item, uint enable);

    [DllImport("user32.dll")]
    private static extern bool DrawMenuBar(IntPtr window);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int valueSize);

    [DllImport("dwmapi.dll")]
    private static extern int DwmExtendFrameIntoClientArea(IntPtr window, ref Margins margins);
}

internal static class ArtifactSave
{
    private const int MaximumBytes = 20_000_000;
    private static readonly UTF8Encoding Utf8 = new(false, true);

    internal static string? ReadString(JsonElement message, string key) =>
        message.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    internal static (string Name, byte[] Data)? ReadContent(JsonElement message, bool binary, bool original = false, bool backup = false)
    {
        var name = ReadString(message, "name");
        var payload = ReadString(message, binary ? "base64" : "text");
        if (string.IsNullOrEmpty(name) || name.Length > 255 || name.Any(char.IsControl)
            || name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || name != Path.GetFileName(name)
            || payload is null || (!original && payload.Length == 0)) return null;
        var extension = Path.GetExtension(name).ToLowerInvariant();
        try
        {
            if (Utf8.GetByteCount(name) > 255) return null;
            if (!binary)
            {
                if (extension is not (".html" or ".json") || Utf8.GetByteCount(payload) > MaximumBytes) return null;
                return (name, Utf8.GetBytes(payload));
            }
            if (backup)
            {
                const int packageLimit = 128 * 1024 * 1024;
                if (extension != ".zip" || payload.Length > (packageLimit + 2) / 3 * 4) return null;
                var package = Convert.FromBase64String(payload);
                return package.Length <= packageLimit && package.AsSpan().StartsWith("PK\u0003\u0004"u8) ? (name, package) : null;
            }
            if (original)
            {
                const int originalLimit = 25 * 1024 * 1024;
                if (extension is not (".pdf" or ".docx" or ".txt" or ".md" or ".html" or ".htm") || payload.Length > (originalLimit + 2) / 3 * 4)
                    return null;
                var originalBytes = Convert.FromBase64String(payload);
                return originalBytes.Length <= originalLimit ? (name, originalBytes) : null;
            }
            if (extension is not (".pdf" or ".docx" or ".xlsx" or ".csv" or ".png") || payload.Length > 28_000_000) return null;
            var data = Convert.FromBase64String(payload);
            if (data.Length == 0 || data.Length > MaximumBytes) return null;
            if (extension == ".pdf" && !data.AsSpan().StartsWith("%PDF-"u8)) return null;
            if ((extension is ".docx" or ".xlsx") && !data.AsSpan().StartsWith("PK"u8)) return null;
            if (extension == ".csv") Utf8.GetCharCount(data);
            if (extension == ".png" && !data.AsSpan().StartsWith(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 })) return null;
            return (name, data);
        }
        catch (Exception error) when (error is FormatException or EncoderFallbackException or DecoderFallbackException)
        {
            return null;
        }
    }

    internal static void Write(string destination, byte[] data)
    {
        var directory = Path.GetDirectoryName(destination) ?? throw new IOException("Missing destination directory");
        var temporary = Path.Combine(directory, ".rh-save-" + Guid.NewGuid().ToString("N") + ".tmp");
        var staged = false;
        try
        {
            using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                staged = true;
                file.Write(data);
                file.Flush(flushToDisk: true);
            }
            if (!File.ReadAllBytes(temporary).AsSpan().SequenceEqual(data))
                throw new IOException("Staged export failed verification");
            File.Move(temporary, destination, overwrite: true);
            staged = false;
        }
        finally
        {
            if (staged) File.Delete(temporary);
        }
    }
}

internal static class UIScalePreferences
{
    internal static double Load(string path, IReadOnlyList<double> allowed)
    {
        try
        {
            if (!File.Exists(path)) return 1.0;
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            if (!root.TryGetProperty("schema_version", out var version) || version.GetInt32() != 1
                || !root.TryGetProperty("ui_scale", out var scale) || !scale.TryGetDouble(out var value)) return 1.0;
            foreach (var step in allowed)
            {
                if (Math.Abs(step - value) < 0.0001) return step;
            }
            return 1.0;
        }
        catch (Exception)
        {
            return 1.0;
        }
    }

    internal static void Save(string path, double scale)
    {
        var temporary = path + ".tmp";
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(temporary, JsonSerializer.Serialize(new { schema_version = 1, ui_scale = scale }, new JsonSerializerOptions { WriteIndented = true }));
            File.Move(temporary, path, true);
        }
        catch (Exception)
        {
            try { if (File.Exists(temporary)) File.Delete(temporary); }
            catch { /* A later save can replace a stale temporary file. */ }
        }
    }
}
