/* ================================================================== *
 * Addon: Chat Stats
 *
 * Shows live message, word, and token stats in the chat sidebar/header.
 * ================================================================== */

ChatAddons.register({
    id: 'workspace:chat-stats',
    name: 'Chat Stats',
    version: '1.0.0',
    description: 'Live chat usage stats for /chat/.',
    author: 'g4f',
    builtin: false,
    permissions: ['dom:write', 'dom:query', 'chat:read', 'storage:local'],

    load() {
        return (async () => {
            this._container = this._createContainer();
            this._refresh = this._refresh.bind(this);
            ChatAddonHost.onMessageRender('workspace:chat-stats', this._refresh);
            this._refresh();
        })();
    },

    unload() {
        if (this._container && this._container.parentNode) {
            this._container.parentNode.removeChild(this._container);
        }
    },

    _createContainer() {
        const container = document.createElement('div');
        container.id = 'pa-chat-stats';
        container.className = 'pa-chat-stats';
        container.innerHTML = `
            <div class="pa-chat-stats-inner">
                <div class="pa-chat-stats-title">Chat Stats</div>
                <div class="pa-chat-stats-row">
                    <span>Messages</span>
                    <strong id="pa-stat-messages">0</strong>
                </div>
                <div class="pa-chat-stats-row">
                    <span>Words</span>
                    <strong id="pa-stat-words">0</strong>
                </div>
                <div class="pa-chat-stats-row">
                    <span>Tokens</span>
                    <strong id="pa-stat-tokens">0</strong>
                </div>
                <div class="pa-chat-stats-row">
                    <span>Est. read time</span>
                    <strong id="pa-stat-readtime">0m</strong>
                </div>
            </div>
        `;

        const target = document.querySelector('.sidebar-container, aside .sidebar-container, .sidebar');
        if (target) {
            target.appendChild(container);
        } else {
            document.body.appendChild(container);
        }

        return container;
    },

    _refresh() {
        const state = ChatAddonHost.getState?.() || {};
        const messages = Array.isArray(state.settings?.messages) ? state.settings.messages : [];
        const visibleMessages = messages.filter((m) => m && typeof m.content === 'string');

        const text = visibleMessages.map((m) => m.content).join(' ');
        const words = text ? text.trim().split(/\s+/).filter(Boolean).length : 0;
        const tokens = this._estimateTokens(text);
        const readMinutes = Math.max(1, Math.round(words / 238));

        this._setText('pa-stat-messages', String(visibleMessages.length));
        this._setText('pa-stat-words', String(words));
        this._setText('pa-stat-tokens', String(tokens));
        this._setText('pa-stat-readtime', `${readMinutes}m`);
    },

    _estimateTokens(text) {
        if (!text) return 0;
        const words = text.trim().split(/\s+/).filter(Boolean).length;
        return Math.round(words * 1.33);
    },

    _setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    },
});
