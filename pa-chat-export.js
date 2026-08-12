/* ================================================================== *
 * Addon: Chat Export
 *
 * Adds quick conversation export actions to the chat UI.
 * ================================================================== */

ChatAddons.register({
    id: 'workspace:chat-export',
    name: 'Chat Export',
    version: '1.0.0',
    description: 'Quick conversation export for /chat/.',
    author: 'g4f',
    builtin: false,
    permissions: ['dom:write', 'dom:query', 'chat:read', 'storage:local', 'ui:notify'],

    load() {
        return (async () => {
            this._container = this._createControls();
        })();
    },

    unload() {
        if (this._container && this._container.parentNode) {
            this._container.parentNode.removeChild(this._container);
        }
    },

    _createControls() {
        const controls = document.createElement('div');
        controls.id = 'pa-chat-export';
        controls.className = 'pa-chat-export';
        controls.innerHTML = `
            <div class="pa-chat-export-inner">
                <div class="pa-chat-export-title">Export</div>
                <button data-format="json" class="pa-chat-export-btn">JSON</button>
                <button data-format="md" class="pa-chat-export-btn">Markdown</button>
                <button data-format="txt" class="pa-chat-export-btn">Text</button>
            </div>
        `;

        const target = document.querySelector('.sidebar-container, aside .sidebar-container, .sidebar');
        if (target) {
            target.appendChild(controls);
        } else {
            document.body.appendChild(controls);
        }

        controls.querySelectorAll('.pa-chat-export-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const format = btn.getAttribute('data-format');
                this._exportConversation(format);
            });
        });

        return controls;
    },

    _exportConversation(format) {
        const state = ChatAddonHost.getState?.() || {};
        const messages = Array.isArray(state.settings?.messages) ? state.settings.messages : [];
        if (!messages.length) {
            ChatAddonHost.notify('No messages to export', 'error');
            return;
        }

        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        let content = '';
        let filename = `chat-export-${timestamp}`;
        let mime = 'text/plain';

        if (format === 'json') {
            content = JSON.stringify({ conversation_id: state.conversation_id, exported_at: timestamp, messages }, null, 2);
            filename += '.json';
            mime = 'application/json';
        } else if (format === 'md') {
            content = messages.map((m) => `### ${this._escape(m.role)}\n\n${this._escape(m.content)}`).join('\n\n---\n\n');
            filename += '.md';
            mime = 'text/markdown';
        } else {
            content = messages.map((m) => `[${this._escape(m.role)}]\n${m.content}`).join('\n\n');
            filename += '.txt';
        }

        this._download(filename, content, mime);
        ChatAddonHost.notify(`Exported ${format.toUpperCase()}`, 'success', 1500);
    },

    _download(filename, content, mime) {
        const blob = new Blob([content], { type: `${mime};charset=utf-8` });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    },

    _escape(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    },
});
