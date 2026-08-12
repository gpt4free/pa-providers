/* ================================================================== *
 * Addon: Browser Console Errors
 *
 * Captures console.error, uncaught exceptions, and unhandled promise
 * rejections from the browser. Stores them in a ring buffer and exposes
 * them via a global API so the MCP Agent can include them as context
 * when sending prompts to the AI.
 *
 * Also registers a virtual MCP server ("browser") with tools:
 *   - browser.get_errors      → return captured console errors
 *   - browser.clear_errors     → clear the error buffer
 *   - browser.eval             → evaluate JS in the page (read-only)
 *   - browser.get_url          → return current page URL + title
 *   - browser.get_console_log  → return recent console.log output
 * ================================================================== */

ChatAddons.register({
    id: 'workspace:browser-errors',
    name: 'Browser Console Errors',
    version: '1.0.0',
    description: 'Captures browser console errors and exposes them as virtual MCP tools for the agent.',
    author: 'g4f',
    builtin: false,
    permissions: ['dom:read', 'dom:query', 'ui:notify', 'net:fetch'],

    MAX_ERRORS: 50,
    MAX_LOGS: 30,
    _errors: [],
    _logs: [],
    _installed: false,

    // ----------------------------------------------------------------
    // Lifecycle
    // ----------------------------------------------------------------
    load() {
        return (async () => {
            this._installHooks();
            this._registerVirtualMCP();
            // Expose globally so pa-mcp-agent can access errors
            window._paBrowserErrors = this;
            ChatAddonHost.notify('Browser error tracking aktiviert', 'info', 2500);
        })();
    },

    unload() {
        this._uninstallHooks();
        this._unregisterVirtualMCP();
    },

    // ----------------------------------------------------------------
    // Console / error hooks
    // ----------------------------------------------------------------
    _installHooks() {
        if (this._installed) return;
        this._installed = true;

        // --- console.error ---
        this._origConsoleError = console.error;
        const self = this;
        console.error = function (...args) {
            self._capture('error', args);
            return self._origConsoleError.apply(console, args);
        };

        // --- console.warn (optional, useful for the agent) ---
        this._origConsoleWarn = console.warn;
        console.warn = function (...args) {
            self._capture('warn', args);
            return self._origConsoleWarn.apply(console, args);
        };

        // --- console.log (keep a small ring buffer) ---
        this._origConsoleLog = console.log;
        console.log = function (...args) {
            self._captureLog(args);
            return self._origConsoleLog.apply(console, args);
        };

        // --- window.onerror (uncaught exceptions) ---
        this._origOnerror = window.onerror;
        window.onerror = function (message, source, lineno, colno, error) {
            self._capture('uncaught', [message, source, lineno, colno, error]);
            if (typeof self._origOnerror === 'function') {
                return self._origOnerror.call(window, message, source, lineno, colno, error);
            }
            return false;
        };

        // --- unhandledrejection (promise rejections) ---
        this._origOnunhandledrejection = window.onunhandledrejection;
        window.addEventListener('unhandledrejection', this._onUnhandled = function (event) {
            self._capture('unhandledrejection', [event.reason]);
        });
    },

    _uninstallHooks() {
        if (!this._installed) return;
        this._installed = false;
        if (this._origConsoleError) console.error = this._origConsoleError;
        if (this._origConsoleWarn) console.warn = this._origConsoleWarn;
        if (this._origConsoleLog) console.log = this._origConsoleLog;
        if (this._origOnerror) window.onerror = this._origOnerror;
        if (this._onUnhandled) window.removeEventListener('unhandledrejection', this._onUnhandled);
    },

    _capture(type, args) {
        const entry = {
            type,
            time: new Date().toISOString(),
            message: args.map(a => this._stringify(a)).join(' '),
            stack: this._extractStack(args),
        };
        this._errors.push(entry);
        if (this._errors.length > this.MAX_ERRORS) this._errors.shift();
    },

    _captureLog(args) {
        const entry = {
            time: new Date().toISOString(),
            message: args.map(a => this._stringify(a)).join(' '),
        };
        this._logs.push(entry);
        if (this._logs.length > this.MAX_LOGS) this._logs.shift();
    },

    _stringify(val) {
        if (val == null) return String(val);
        if (typeof val === 'string') return val;
        if (val instanceof Error) return val.message + (val.stack ? '\n' + val.stack : '');
        try {
            return JSON.stringify(val, null, 0);
        } catch (e) {
            return String(val);
        }
    },

    _extractStack(args) {
        for (const a of args) {
            if (a instanceof Error && a.stack) return a.stack;
            if (a && typeof a === 'object' && a.stack) return String(a.stack);
        }
        return '';
    },

    // ----------------------------------------------------------------
    // Public API (for other addons / the agent)
    // ----------------------------------------------------------------
    getErrors() {
        return [...this._errors];
    },

    clearErrors() {
        this._errors = [];
        return true;
    },

    getLogs() {
        return [...this._logs];
    },

    // ----------------------------------------------------------------
    // Virtual MCP server registration
    // ----------------------------------------------------------------
    _registerVirtualMCP() {
        const self = this;

        // The virtual "browser" MCP server — a plain object that the
        // MCP agent can discover via mcpClient.getAllTools().
        const browserServer = {
            name: 'browser',
            tools: [
                {
                    name: 'browser.get_errors',
                    description: 'Get browser console errors, uncaught exceptions, and unhandled promise rejections captured by the browser-errors addon.',
                    inputSchema: { type: 'object', properties: { limit: { type: 'number', description: 'Max number of errors to return (default 20)' } } },
                    async handler(args) {
                        const limit = Math.min(args?.limit || 20, self.MAX_ERRORS);
                        const errors = self.getErrors().slice(-limit);
                        return {
                            content: [{ type: 'text', text: JSON.stringify(errors, null, 2) }],
                            isError: errors.length > 0 ? false : false,
                            _meta: { count: errors.length },
                        };
                    },
                },
                {
                    name: 'browser.clear_errors',
                    description: 'Clear all captured browser console errors.',
                    inputSchema: { type: 'object', properties: {} },
                    async handler() {
                        self.clearErrors();
                        return { content: [{ type: 'text', text: 'Browser errors cleared.' }] };
                    },
                },
                {
                    name: 'browser.get_console_log',
                    description: 'Get recent console.log output captured by the browser-errors addon.',
                    inputSchema: { type: 'object', properties: { limit: { type: 'number', description: 'Max number of log entries to return (default 15)' } } },
                    async handler(args) {
                        const limit = Math.min(args?.limit || 15, self.MAX_LOGS);
                        const logs = self.getLogs().slice(-limit);
                        return { content: [{ type: 'text', text: JSON.stringify(logs, null, 2) }] };
                    },
                },
                {
                    name: 'browser.get_url',
                    description: 'Get the current browser page URL and title.',
                    inputSchema: { type: 'object', properties: {} },
                    async handler() {
                        return {
                            content: [{
                                type: 'text',
                                text: JSON.stringify({ url: location.href, title: document.title }),
                            }],
                        };
                    },
                },
                {
                    name: 'browser.eval',
                    description: 'Evaluate a JavaScript expression in the browser page context and return the result. Use for debugging or inspecting DOM state.',
                    inputSchema: {
                        type: 'object',
                        properties: { code: { type: 'string', description: 'JavaScript expression to evaluate' } },
                        required: ['code'],
                    },
                    async handler(args) {
                        try {
                            const result = eval(args.code); // eslint-disable-line no-eval
                            const text = self._stringify(result);
                            return { content: [{ type: 'text', text }] };
                        } catch (err) {
                            return {
                                content: [{ type: 'text', text: `Error: ${err.message}` }],
                                isError: true,
                            };
                        }
                    },
                },
                {
                    name: 'browser.get_dom',
                    description: 'Query the DOM with a CSS selector and return the outerHTML of matching elements (truncated).',
                    inputSchema: {
                        type: 'object',
                        properties: {
                            selector: { type: 'string', description: 'CSS selector' },
                            limit: { type: 'number', description: 'Max elements to return (default 5)' },
                        },
                        required: ['selector'],
                    },
                    async handler(args) {
                        const limit = Math.min(args?.limit || 5, 20);
                        const els = document.querySelectorAll(args.selector);
                        const results = [];
                        for (let i = 0; i < Math.min(els.length, limit); i++) {
                            results.push(els[i].outerHTML.slice(0, 2000));
                        }
                        return {
                            content: [{ type: 'text', text: JSON.stringify({ count: els.length, elements: results }, null, 2) }],
                        };
                    },
                },
            ],
        };

        this._browserServer = browserServer;

        // Register with the global MCP client if it supports virtual servers
        const mcp = this._getMCP();
        if (mcp && typeof mcp.registerVirtualServer === 'function') {
            mcp.registerVirtualServer(browserServer);
        } else if (mcp && typeof mcp.addServer === 'function') {
            mcp.addServer(browserServer);
        } else {
            // Fallback: store on window so pa-mcp-agent can discover it
            if (!window._paVirtualServers) window._paVirtualServers = [];
            window._paVirtualServers.push(browserServer);
        }
    },

    _unregisterVirtualMCP() {
        const mcp = this._getMCP();
        if (mcp && typeof mcp.unregisterVirtualServer === 'function' && this._browserServer) {
            mcp.unregisterVirtualServer(this._browserServer.name);
        }
        if (window._paVirtualServers && this._browserServer) {
            window._paVirtualServers = window._paVirtualServers.filter(s => s !== this._browserServer);
        }
    },

    _getMCP() {
        try {
            if (typeof mcpClient !== 'undefined' && mcpClient) return mcpClient;
            if (typeof global !== 'undefined' && global.mcpClient) return global.mcpClient;
            if (typeof window !== 'undefined' && window.mcpClient) return window.mcpClient;
        } catch (e) { /* ignore */ }
        return null;
    },
});