/* ================================================================== *
 * Addon: IndexedDB Suggestions
 *
 * Scans the entire chat IndexedDB (chat-db / conversations store),
 * extracts all messages, counts the most frequently used sentences
 * and phrases, and shows them as clickable suggestion chips above
 * the chat input. Clicking a chip inserts the text into the input box.
 *
 * Uses: dom:read, dom:write, storage:local, ui:notify
 * ================================================================== */

ChatAddons.register({
    id: 'workspace:indexeddb-suggestions',
    name: 'IndexedDB Suggestions',
    version: '1.0.0',
    description: 'Scans your chat history (IndexedDB) and suggests your most-used sentences as clickable chips.',
    author: 'g4f',
    builtin: false,
    permissions: ['dom:read', 'dom:write', 'storage:local', 'ui:notify'],

    STORAGE_KEY: 'chat.addons.data.pa-indexeddb-suggestions.config',
    CHIP_LIMIT: 8,

    load() {
        return (async () => {
            await this._injectBar();
            this._refreshSuggestions();
        })();
    },

    unload() {
        const bar = document.getElementById('pa-indexeddb-suggestions-bar');
        if (bar) bar.remove();
    },

    // ------------------------------------------------------------------
    // UI injection
    // ------------------------------------------------------------------
    _injectBar() {
        return new Promise((resolve) => {
            const tryInject = () => {
                const inputArea = document.querySelector('.input-area, .chat-input-area, #chat-input-area, main .bottom, .message-input-area');
                if (!inputArea) {
                    setTimeout(tryInject, 300);
                    return;
                }

                if (document.getElementById('pa-indexeddb-suggestions-bar')) {
                    resolve();
                    return;
                }

                const bar = document.createElement('div');
                bar.id = 'pa-indexeddb-suggestions-bar';
                bar.className = 'pa-indexeddb-suggestions';
                bar.innerHTML = `
                    <div class="pa-idb-suggestions-inner">
                        <span class="pa-idb-suggestions-label" title="Based on your most frequent sentences">Suggestions</span>
                        <div class="pa-idb-suggestions-chips"></div>
                    </div>
                `;

                inputArea.parentNode.insertBefore(bar, inputArea);

                this._chipsEl = bar.querySelector('.pa-idb-suggestions-chips');

                resolve();
            };

            tryInject();
        });
    },

    // ------------------------------------------------------------------
    // IndexedDB scanning
    // ------------------------------------------------------------------
    _openDB() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open('chat-db', 1);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    },

    _getAllConversations(db) {
        return new Promise((resolve, reject) => {
            const tx = db.transaction('conversations', 'readonly');
            const store = tx.objectStore('conversations');
            const all = [];
            const cursorReq = store.openCursor();
            cursorReq.onsuccess = () => {
                const cursor = cursorReq.result;
                if (cursor) {
                    all.push(cursor.value);
                    cursor.continue();
                } else {
                    resolve(all);
                }
            };
            cursorReq.onerror = () => reject(cursorReq.error);
        });
    },

    async _collectMessages() {
        try {
            const db = await this._openDB();
            const conversations = list_conversations ? await list_conversations() : await this._getAllConversations(db);
            let texts = [];
            for (const conv of conversations) {
                const messages = conv.messages || conv.items || [];
                for (const msg of messages) {
                    let content = msg.content;
                    if (Array.isArray(content)) {
                        content = content
                            .filter(part => part && part.type === 'text')
                            .map(part => part.text)
                            .join(' ');
                    }
                    if (typeof content === 'string' && content.trim()) {
                        texts.push(content.trim());
                    }
                }
            }
            texts = texts.map(t => {
                return t.replace(/```.?\n.+?```\n|try {.+}\n?|<.+>/gs, '').trim();
            });
            return texts;
        } catch (err) {
            console.error('[indexeddb-suggestions] scan error', err);
            return [];
        }
    },

    // ------------------------------------------------------------------
    // Sentence/phrase extraction & counting
    // ------------------------------------------------------------------
    _splitSentences(text) {
        return text
            .split(/(?<=[.!?…])\s+|\n+/)
            .map(s => s.trim())
            .filter(s => s.length >= 4 && s.length <= 160);
    },

    _countPhrases(texts) {
        const counts = new Map();
        const add = (phrase) => {
            phrase = phrase.replace(/[|*<-\s#]+/g, ' ').trim();
            if (!phrase) return;
            const key = phrase.toLowerCase();
            counts.set(key, (counts.get(key) || 0) + 1);
        };

        for (const text of texts) {
            const sentences = this._splitSentences(text);
            // Whole sentences
            for (const s of sentences) {
                add(s);
            }
            // Common phrase patterns: last clause of each sentence
            for (const s of sentences) {
                const clauses = s.split(/,|;|:/).map(c => c.trim()).filter(c => c.length >= 8 && c.length <= 120);
                for (const c of clauses) {
                    add(c);
                }
            }
        }

        // Build ranked list: most frequent first, then by length desc
        return [...counts.entries()]
            .sort((a, b) => (b[1] - a[1]) || (b[0].length - a[0].length))
            .slice(0, this.CHIP_LIMIT);
    },

    _refreshSuggestions() {
        this._collectMessages().then((texts) => {
            if (!this._chipsEl) return;
            const ranked = this._countPhrases(texts);
            if (ranked.length === 0) {
                this._chipsEl.innerHTML = '<span class="pa-idb-chip pa-idb-chip-empty">Noch keine Sätze gefunden – chatte erst ein bisschen.</span>';
                return;
            }
            this._chipsEl.innerHTML = '';
            for (const [phrase] of ranked) {
                const chip = document.createElement('button');
                chip.className = 'pa-idb-chip';
                chip.title = phrase;
                chip.textContent = phrase.length > 42 ? phrase.slice(0, 42) + '…' : phrase;
                chip.addEventListener('click', () => this._insertIntoInput(phrase));
                this._chipsEl.appendChild(chip);
            }
        });
    },

    // ------------------------------------------------------------------
    // Insert into input
    // ------------------------------------------------------------------
    _insertIntoInput(text) {
        const userInput = document.querySelector('#user-input, #userInput, textarea[name="user_input"], .chat-input textarea, .input-area textarea');
        if (userInput) {
            userInput.value = text;
            userInput.dispatchEvent(new Event('input', { bubbles: true }));
            userInput.focus();
            ChatAddonHost.notify('Vorschlag eingefügt', 'success', 1200);
        } else {
            ChatAddonHost.notify('Chat-Eingabe nicht gefunden', 'error');
        }
    },
});

// Minimal CSS injection (guarded — idempotent)
(function injectIdbCss() {
    if (document.getElementById('pa-indexeddb-suggestions-css')) return;
    const style = document.createElement('style');
    style.id = 'pa-indexeddb-suggestions-css';
    style.textContent = `
.pa-indexeddb-suggestions { display: flex; align-items: center; gap: 8px; padding: 6px 10px; margin-bottom: 6px; background: rgba(255,255,255,.03); border: 1px solid var(--blur-border, #333); border-radius: 10px; max-width: 100%; }
.pa-indexeddb-suggestions .pa-idb-suggestions-label { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; opacity: .55; white-space: nowrap; }
.pa-indexeddb-suggestions-chips { display: flex; flex-wrap: wrap; gap: 6px; overflow: hidden; }
.pa-idb-chip { padding: 4px 10px; border-radius: 14px; border: 1px solid var(--blur-border, #333); background: var(--input-bg, #1a1525); color: var(--colour-3, #e0e0e0); font-size: 12px; cursor: pointer; white-space: nowrap; max-width: 260px; overflow: hidden; text-overflow: ellipsis; transition: border-color .15s, background .15s; }
.pa-idb-chip:hover { border-color: var(--accent, #8b3dff); background: rgba(139,61,255,.12); }
.pa-idb-chip-empty { cursor: default; opacity: .6; border-style: dashed; }
`;
    document.head.appendChild(style);
})();
