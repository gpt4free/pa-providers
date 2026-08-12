/* ================================================================== *
 * Addon: Prompt Sets
 *
 * Lets you define your own prompt sets. Each prompt can be either
 * sent directly ("Direkt senden") or inserted into the input box
 * first ("In Input-Box einfügen"). Persisted via ChatAddonHost.storage.
 *
 * Uses: dom:read, dom:write, storage:local, ui:notify
 * ================================================================== */

ChatAddons.register({
    id: 'workspace:prompt-sets',
    name: 'Prompt Sets',
    version: '1.0.0',
    description: 'Eigene Prompt-Sets verwalten – direkt absenden oder erst in die Input-Box einfügen.',
    author: 'g4f',
    builtin: false,
    permissions: ['dom:read', 'dom:write', 'storage:local', 'ui:notify'],

    STORAGE_KEY: 'promptSets',

    load() {
        return (async () => {
            this._sets = (await this._loadSets()) || [];
            await this._injectPanel();
        })();
    },

    unload() {
        const panel = document.getElementById('pa-prompt-sets-panel');
        if (panel) panel.remove();
        const css = document.getElementById('pa-prompt-sets-css');
        if (css) css.remove();
    },

    // ------------------------------------------------------------------
    // Persistence (namespaced storage via host bridge)
    // ------------------------------------------------------------------
    async _loadSets() {
        try {
            if (typeof ChatAddonHost?.storage?.getJSON === 'function') {
                return await ChatAddonHost.storage.getJSON(this.STORAGE_KEY);
            }
            return JSON.parse(localStorage.getItem('chat.addons.data.' + this.STORAGE_KEY) || 'null');
        } catch (e) {
            console.error('[prompt-sets] load error', e);
            return [];
        }
    },

    async _saveSets() {
        try {
            if (typeof ChatAddonHost?.storage?.setJSON === 'function') {
                await ChatAddonHost.storage.setJSON(this.STORAGE_KEY, this._sets);
            } else {
                localStorage.setItem('chat.addons.data.' + this.STORAGE_KEY, JSON.stringify(this._sets));
            }
        } catch (e) {
            console.error('[prompt-sets] save error', e);
        }
    },

    // ------------------------------------------------------------------
    // Panel UI
    // ------------------------------------------------------------------
    _injectPanel() {
        return new Promise((resolve) => {
            const tryInject = () => {
                const inputArea = document.querySelector('.input-area, .chat-input-area, #chat-input-area, main .bottom, .message-input-area');
                if (!inputArea) {
                    setTimeout(tryInject, 300);
                    return;
                }

                if (document.getElementById('pa-prompt-sets-panel')) {
                    resolve();
                    return;
                }

                const panel = document.createElement('div');
                panel.id = 'pa-prompt-sets-panel';
                panel.className = 'pa-prompt-sets';
                panel.innerHTML = `
                    <div class="pa-ps-header">
                        <span class="pa-ps-title">Prompt Sets</span>
                        <div class="pa-ps-actions">
                            <button class="pa-ps-btn" id="pa-ps-manage">Verwalten</button>
                        </div>
                    </div>
                    <div class="pa-ps-chips" id="pa-ps-chips"></div>
                `;

                inputArea.parentNode.insertBefore(panel, inputArea);

                panel.querySelector('#pa-ps-manage').addEventListener('click', () => this._openManager());

                this._renderChips();
                resolve();
            };

            tryInject();
        });
    },

    _renderChips() {
        const chipsEl = document.getElementById('pa-ps-chips');
        if (!chipsEl) return;
        chipsEl.innerHTML = '';
        if (!this._sets || this._sets.length === 0) {
            chipsEl.innerHTML = '<span class="pa-ps-empty">Keine Prompts – klicke auf "Verwalten", um eigene zu erstellen.</span>';
            return;
        }
        for (const set of this._sets) {
            if (!set.enabled) continue;
            const chip = document.createElement('button');
            chip.className = 'pa-ps-chip';
            chip.textContent = set.label;
            chip.title = set.prompt;
            chip.addEventListener('click', () => this._applyPrompt(set));
            chipsEl.appendChild(chip);
        }
    },

    _applyPrompt(set) {
        const userInput = document.querySelector('#user-input, #userInput, textarea[name="user_input"], .chat-input textarea, .input-area textarea');
        if (!userInput) {
            ChatAddonHost.notify('Chat-Eingabe nicht gefunden', 'error');
            return;
        }
        userInput.value = set.prompt;
        userInput.dispatchEvent(new Event('input', { bubbles: true }));

        if (set.send) {
            // Direkt absenden
            const sendButton = document.querySelector('#send-button, .send-button, button[aria-label="Send"], #sendButton');
            if (sendButton) {
                sendButton.click();
            } else {
                // Fallback: Enter-Taste im Textarea (falls erlaubt)
                userInput.focus();
                userInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
            }
            ChatAddonHost.notify(`Gesendet: ${set.label}`, 'success', 1500);
        } else {
            // Erst in die Input-Box einfügen
            userInput.focus();
            ChatAddonHost.notify(`Eingefügt: ${set.label}`, 'success', 1500);
        }
    },

    // ------------------------------------------------------------------
    // Manager overlay
    // ------------------------------------------------------------------
    _openManager() {
        if (document.getElementById('pa-ps-manager-overlay')) {
            document.getElementById('pa-ps-manager-overlay').classList.add('open');
            this._renderManagerList();
            return;
        }

        const overlay = document.createElement('div');
        overlay.id = 'pa-ps-manager-overlay';
        overlay.className = 'pa-ps-overlay';
        overlay.innerHTML = `
            <div class="pa-ps-manager-panel">
                <div class="pa-ps-manager-head">
                    <h3>Prompt Sets verwalten</h3>
                    <button class="pa-ps-close" id="pa-ps-close">&times;</button>
                </div>
                <div class="pa-ps-manager-body">
                    <div class="pa-ps-form">
                        <input type="text" id="pa-ps-label" placeholder="Name / Label" />
                        <textarea id="pa-ps-prompt" placeholder="Prompt-Text…" rows="3"></textarea>
                        <div class="pa-ps-form-row">
                            <label class="pa-ps-check">
                                <input type="checkbox" id="pa-ps-send" />
                                Direkt absenden
                            </label>
                            <button class="pa-ps-btn primary" id="pa-ps-add">Hinzufügen</button>
                        </div>
                    </div>
                    <div class="pa-ps-list" id="pa-ps-list"></div>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.classList.remove('open');
        });
        overlay.querySelector('#pa-ps-close').addEventListener('click', () => overlay.classList.remove('open'));

        overlay.querySelector('#pa-ps-add').addEventListener('click', () => {
            const label = overlay.querySelector('#pa-ps-label').value.trim();
            const prompt = overlay.querySelector('#pa-ps-prompt').value.trim();
            const send = overlay.querySelector('#pa-ps-send').checked;
            if (!label || !prompt) {
                ChatAddonHost.notify('Label und Prompt sind Pflichtfelder', 'error');
                return;
            }
            this._sets.push({ label, prompt, send, enabled: true });
            this._saveSets();
            this._renderManagerList();
            this._renderChips();
            overlay.querySelector('#pa-ps-label').value = '';
            overlay.querySelector('#pa-ps-prompt').value = '';
            overlay.querySelector('#pa-ps-send').checked = false;
            ChatAddonHost.notify('Prompt hinzugefügt', 'success', 1500);
        });

        this._renderManagerList();
    },

    _renderManagerList() {
        const listEl = document.getElementById('pa-ps-list');
        if (!listEl) return;
        listEl.innerHTML = '';
        if (!this._sets || this._sets.length === 0) {
            listEl.innerHTML = '<div class="pa-ps-empty">Noch keine Prompts.</div>';
            return;
        }
        this._sets.forEach((set, idx) => {
            const row = document.createElement('div');
            row.className = 'pa-ps-row';
            row.innerHTML = `
                <div class="pa-ps-row-info">
                    <div class="pa-ps-row-label">${this._escape(set.label)} <span class="pa-ps-row-badge">${set.send ? 'senden' : 'einfügen'}</span></div>
                    <div class="pa-ps-row-prompt">${this._escape(set.prompt)}</div>
                </div>
                <div class="pa-ps-row-actions">
                    <label class="pa-ps-check" title="Aktiv/inaktiv">
                        <input type="checkbox" ${set.enabled ? 'checked' : ''} />
                    </label>
                    <button class="pa-ps-row-btn" data-act="delete" title="Löschen">🗑</button>
                </div>
            `;

            const cb = row.querySelector('input[type="checkbox"]');
            cb.addEventListener('change', () => {
                set.enabled = cb.checked;
                this._saveSets();
                this._renderChips();
            });

            row.querySelector('[data-act="delete"]').addEventListener('click', () => {
                this._sets.splice(idx, 1);
                this._saveSets();
                this._renderManagerList();
                this._renderChips();
                ChatAddonHost.notify('Prompt gelöscht', 'info', 1500);
            });

            listEl.appendChild(row);
        });
    },

    _escape(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    },
});

// Minimal CSS injection (guarded — idempotent)
(function injectPromptSetsCss() {
    if (document.getElementById('pa-prompt-sets-css')) return;
    const style = document.createElement('style');
    style.id = 'pa-prompt-sets-css';
    style.textContent = `
.pa-prompt-sets { padding: 6px 10px; margin-bottom: 6px; background: rgba(255,255,255,.03); border: 1px solid var(--blur-border, #333); border-radius: 10px; }
.pa-ps-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.pa-ps-title { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; opacity: .55; }
.pa-ps-actions { display: flex; gap: 6px; }
.pa-ps-btn { padding: 3px 10px; border-radius: 6px; border: 1px solid var(--blur-border, #333); background: transparent; color: inherit; font-size: 11px; cursor: pointer; }
.pa-ps-btn:hover { background: rgba(255,255,255,.1); }
.pa-ps-btn.primary { background: var(--accent, #8b3dff); color: #fff; border-color: transparent; }
.pa-ps-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.pa-ps-chip { padding: 4px 12px; border-radius: 14px; border: 1px solid var(--blur-border, #333); background: var(--input-bg, #1a1525); color: var(--colour-3, #e0e0e0); font-size: 12px; cursor: pointer; transition: border-color .15s, background .15s; }
.pa-ps-chip:hover { border-color: var(--accent, #8b3dff); background: rgba(139,61,255,.12); }
.pa-ps-empty { font-size: 12px; opacity: .6; font-style: italic; }
.pa-ps-overlay { position: fixed; inset: 0; z-index: 10050; display: none; align-items: center; justify-content: center; background: rgba(0,0,0,.55); }
.pa-ps-overlay.open { display: flex; }
.pa-ps-manager-panel { width: min(560px, 94vw); max-height: 82vh; background: var(--colour-1, #1e1e2e); color: var(--colour-3, #e0e0e0); border: 1px solid var(--blur-border, #333); border-radius: 14px; display: flex; flex-direction: column; overflow: hidden; font-family: inherit; }
.pa-ps-manager-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--blur-border, #333); }
.pa-ps-manager-head h3 { margin: 0; font-size: 16px; }
.pa-ps-close { background: none; border: none; color: inherit; font-size: 20px; cursor: pointer; opacity: .7; }
.pa-ps-close:hover { opacity: 1; }
.pa-ps-manager-body { padding: 14px 18px; overflow-y: auto; }
.pa-ps-form { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
.pa-ps-form input, .pa-ps-form textarea { width: 100%; padding: 8px 10px; border: 1px solid var(--blur-border, #333); border-radius: 8px; background: var(--input-bg, #1a1525); color: var(--colour-3, #e0e0e0); font-size: 13px; box-sizing: border-box; font-family: inherit; }
.pa-ps-form-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.pa-ps-check { display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer; }
.pa-ps-list { display: flex; flex-direction: column; gap: 8px; }
.pa-ps-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; border: 1px solid var(--blur-border, #333); border-radius: 10px; background: rgba(255,255,255,.02); }
.pa-ps-row-info { min-width: 0; flex: 1; }
.pa-ps-row-label { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
.pa-ps-row-badge { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: rgba(139,61,255,.18); color: var(--accent, #8b3dff); font-weight: 500; }
.pa-ps-row-prompt { font-size: 12px; opacity: .65; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pa-ps-row-actions { display: flex; align-items: center; gap: 8px; }
.pa-ps-row-btn { background: none; border: none; cursor: pointer; font-size: 14px; opacity: .6; }
.pa-ps-row-btn:hover { opacity: 1; }
`;
    document.head.appendChild(style);
})();
