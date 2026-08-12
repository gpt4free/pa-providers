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
 *   - browser.get_dom          → query the DOM (read-only)
 *
 * The virtual server is ALWAYS exposed via `window._paVirtualServers`
 * (in addition to `mcpClient.registerVirtualServer` if available) so
 * that pa-mcp-agent.js and pa-coding.js can discover the tools even
 * when the MCP client does not support virtual server registration.
 * ================================================================== */

(function () {
    // ------------------------------------------------------------------
    // Safe accessors – the addon host may not be loaded yet.
    // ------------------------------------------------------------------
    function safeNotify(type, msg, ms) {
        try {
            if (typeof ChatAddonHost !== 'undefined' && ChatAddonHost && typeof ChatAddonHost.notify === 'function') {
                ChatAddonHost.notify(msg, type, ms);
            } else {
                console[type === 'error' ? 'error' : 'log']('[browser-errors]', msg);
            }
        } catch (e) { /* ignore */ }
    }

    function getMCPClient() {
        try {
            // Page-scope globals first (same pattern as the other addons)
            if (typeof window !== 'undefined' && window.mcpClient) return window.mcpClient;
            if (typeof mcpClient !== 'undefined' && mcpClient) return mcpClient;
            if (typeof global !== 'undefined' && global.mcpClient) return global.mcpClient;
        } catch (e) { /* ignore */ }
        return null;
    }

    // Register into the shared virtual-server registry (dedupe by name).
    function registerVirtualServer(server) {
        if (!server || !server.name) return;
        try {
            if (typeof window === 'undefined') return;
            if (!Array.isArray(window._paVirtualServers)) window._paVirtualServers = [];
            // Replace an existing server with the same name (avoids duplicates on reload)
            window._paVirtualServers = window._paVirtualServers.filter(function (s) { return !s || s.name !== server.name; });
            window._paVirtualServers.push(server);
        } catch (e) { /* ignore */ }
    }

    function unregisterVirtualServer(name) {
        try {
            if (typeof window === 'undefined' || !Array.isArray(window._paVirtualServers)) return;
            window._paVirtualServers = window._paVirtualServers.filter(function (s) { return !s || s.name !== name; });
        } catch (e) { /* ignore */ }
    }

    var addon = {
        id: 'workspace:browser-errors',
        name: 'Browser Console Errors',
        version: '1.1.0',
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
        load: function () {
            return (async function () {
                this._installHooks();
                this._registerVirtualMCP();
                // Expose globally so pa-mcp-agent can access errors
                if (typeof window !== 'undefined') window._paBrowserErrors = this;
                safeNotify('info', 'Browser error tracking aktiviert', 2500);
            }).call(this);
        },

        unload: function () {
            this._uninstallHooks();
            this._unregisterVirtualMCP();
            if (typeof window !== 'undefined' && window._paBrowserErrors === this) {
                delete window._paBrowserErrors;
            }
        },

        // ----------------------------------------------------------------
        // Console / error hooks
        // ----------------------------------------------------------------
        _installHooks: function () {
            if (this._installed) return;
            if (typeof window === 'undefined' || typeof console === 'undefined') return;
            this._installed = true;

            var self = this;

            // --- console.error ---
            this._origConsoleError = console.error;
            console.error = function () {
                self._capture('error', Array.prototype.slice.call(arguments));
                return self._origConsoleError.apply(console, arguments);
            };

            // --- console.warn (optional, useful for the agent) ---
            this._origConsoleWarn = console.warn;
            console.warn = function () {
                self._capture('warn', Array.prototype.slice.call(arguments));
                return self._origConsoleWarn.apply(console, arguments);
            };

            // --- console.log (keep a small ring buffer) ---
            this._origConsoleLog = console.log;
            console.log = function () {
                self._captureLog(Array.prototype.slice.call(arguments));
                return self._origConsoleLog.apply(console, arguments);
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
            this._onUnhandled = function (event) {
                self._capture('unhandledrejection', [event && event.reason]);
            };
            window.addEventListener('unhandledrejection', this._onUnhandled);
        },

        _uninstallHooks: function () {
            if (!this._installed) return;
            this._installed = false;
            if (typeof console === 'undefined' || typeof window === 'undefined') return;
            if (this._origConsoleError) console.error = this._origConsoleError;
            if (this._origConsoleWarn) console.warn = this._origConsoleWarn;
            if (this._origConsoleLog) console.log = this._origConsoleLog;
            if (this._origOnerror) window.onerror = this._origOnerror;
            if (this._origOnunhandledrejection) window.onunhandledrejection = this._origOnunhandledrejection;
            if (this._onUnhandled) window.removeEventListener('unhandledrejection', this._onUnhandled);
            this._origConsoleError = this._origConsoleWarn = this._origConsoleLog = null;
            this._origOnerror = this._origOnunhandledrejection = this._onUnhandled = null;
        },

        _capture: function (type, args) {
            var entry = {
                type: type,
                time: new Date().toISOString(),
                message: (args || []).map(function (a) { return this._stringify(a); }, this).join(' '),
                stack: this._extractStack(args || []),
            };
            this._errors.push(entry);
            if (this._errors.length > this.MAX_ERRORS) this._errors.shift();
        },

        _captureLog: function (args) {
            var entry = {
                time: new Date().toISOString(),
                message: (args || []).map(function (a) { return this._stringify(a); }, this).join(' '),
            };
            this._logs.push(entry);
            if (this._logs.length > this.MAX_LOGS) this._logs.shift();
        },

        _stringify: function (val) {
            if (val == null) return String(val);
            if (typeof val === 'string') return val;
            if (val instanceof Error) return val.message + (val.stack ? '\n' + val.stack : '');
            try {
                return JSON.stringify(val);
            } catch (e) {
                return String(val);
            }
        },

        _extractStack: function (args) {
            for (var i = 0; i < args.length; i++) {
                var a = args[i];
                if (a instanceof Error && a.stack) return a.stack;
                if (a && typeof a === 'object' && a.stack) return String(a.stack);
            }
            return '';
        },

        // ----------------------------------------------------------------
        // Public API (for other addons / the agent)
        // ----------------------------------------------------------------
        getErrors: function () {
            return this._errors.slice();
        },

        clearErrors: function () {
            this._errors = [];
            return true;
        },

        getLogs: function () {
            return this._logs.slice();
        },

        // ----------------------------------------------------------------
        // Virtual MCP server registration
        // ----------------------------------------------------------------
        _registerVirtualMCP: function () {
            var self = this;

            var browserServer = {
                name: 'browser',
                tools: [
                    {
                        name: 'browser.get_errors',
                        description: 'Get browser console errors, uncaught exceptions, and unhandled promise rejections captured by the browser-errors addon.',
                        inputSchema: { type: 'object', properties: { limit: { type: 'number', description: 'Max number of errors to return (default 20)' } } },
                        handler: function (args) {
                            var limit = Math.min((args && args.limit) || 20, self.MAX_ERRORS);
                            var errors = self.getErrors().slice(-limit);
                            return Promise.resolve({
                                content: [{ type: 'text', text: JSON.stringify(errors, null, 2) }],
                                isError: false,
                            });
                        },
                    },
                    {
                        name: 'browser.clear_errors',
                        description: 'Clear all captured browser console errors.',
                        inputSchema: { type: 'object', properties: {} },
                        handler: function () {
                            self.clearErrors();
                            return Promise.resolve({ content: [{ type: 'text', text: 'Browser errors cleared.' }] });
                        },
                    },
                    {
                        name: 'browser.get_console_log',
                        description: 'Get recent console.log output captured by the browser-errors addon.',
                        inputSchema: { type: 'object', properties: { limit: { type: 'number', description: 'Max number of log entries to return (default 15)' } } },
                        handler: function (args) {
                            var limit = Math.min((args && args.limit) || 15, self.MAX_LOGS);
                            var logs = self.getLogs().slice(-limit);
                            return Promise.resolve({ content: [{ type: 'text', text: JSON.stringify(logs, null, 2) }] });
                        },
                    },
                    {
                        name: 'browser.get_url',
                        description: 'Get the current browser page URL and title.',
                        inputSchema: { type: 'object', properties: {} },
                        handler: function () {
                            var info = { url: location.href, title: document.title };
                            return Promise.resolve({ content: [{ type: 'text', text: JSON.stringify(info) }] });
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
                        handler: function (args) {
                            try {
                                // eslint-disable-next-line no-eval
                                var result = eval(String(args && args.code || ''));
                                return Promise.resolve({ content: [{ type: 'text', text: self._stringify(result) }] });
                            } catch (err) {
                                return Promise.resolve({
                                    content: [{ type: 'text', text: 'Error: ' + (err && err.message ? err.message : err) }],
                                    isError: true,
                                });
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
                        handler: function (args) {
                            try {
                                var limit = Math.min((args && args.limit) || 5, 20);
                                var els = document.querySelectorAll(String(args && args.selector || ''));
                                var results = [];
                                for (var i = 0; i < Math.min(els.length, limit); i++) {
                                    results.push(els[i].outerHTML.slice(0, 2000));
                                }
                                return Promise.resolve({
                                    content: [{ type: 'text', text: JSON.stringify({ count: els.length, elements: results }, null, 2) }],
                                });
                            } catch (err) {
                                return Promise.resolve({
                                    content: [{ type: 'text', text: 'Error: ' + (err && err.message ? err.message : err) }],
                                    isError: true,
                                });
                            }
                        },
                    },
                ],
            };

            this._browserServer = browserServer;

            // Always expose via the shared registry so the agent addons
            // (pa-mcp-agent.js, pa-coding.js) can discover the tools.
            registerVirtualServer(browserServer);

            // Additionally register with the global MCP client if it
            // supports virtual servers.
            var mcp = getMCPClient();
            if (mcp) {
                if (typeof mcp.registerVirtualServer === 'function') {
                    try { mcp.registerVirtualServer(browserServer); } catch (e) { /* ignore */ }
                } else if (typeof mcp.addServer === 'function') {
                    try { mcp.addServer(browserServer); } catch (e) { /* ignore */ }
                }
            }
        },

        _unregisterVirtualMCP: function () {
            var mcp = getMCPClient();
            if (mcp && typeof mcp.unregisterVirtualServer === 'function' && this._browserServer) {
                try { mcp.unregisterVirtualServer(this._browserServer.name); } catch (e) { /* ignore */ }
            }
            if (this._browserServer) {
                unregisterVirtualServer(this._browserServer.name);
            }
            this._browserServer = null;
        },
    };

    // ------------------------------------------------------------------
    // Register with the addon host (defensively – the host may not be
    // ready yet, in which case we retry after DOMContentLoaded).
    // ------------------------------------------------------------------
    function register() {
        if (typeof ChatAddons !== 'undefined' && ChatAddons && typeof ChatAddons.register === 'function') {
            ChatAddons.register(addon);
            return true;
        }
        return false;
    }

    if (!register()) {
        if (typeof document !== 'undefined') {
            var onReady = function () {
                register();
                document.removeEventListener('DOMContentLoaded', onReady);
            };
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', onReady);
            } else {
                setTimeout(onReady, 0);
            }
        } else {
            // No DOM (worker-ish context) – try once more synchronously
            register();
        }
    }
})();
