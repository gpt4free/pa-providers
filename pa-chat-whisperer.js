/* ================================================================== *
 * Addon: Chat Whisperer
 *
 * Voice input for /chat/. Adds a floating mic button next to the chat
 * input. Hold conversations hands-free: speech is transcribed live
 * into the message box using the browser's Web Speech API.
 *
 * - Click mic to start / stop dictation
 * - Interim results appear in a live caption bubble above the input
 * - Final words are appended to whatever is already typed
 * - Alt+M toggles dictation from the keyboard
 * - Language follows the UI language (navigator.language)
 * ================================================================== */

ChatAddons.register({
    id: 'workspace:chat-whisperer',
    name: 'Chat Whisperer',
    version: '1.0.0',
    description: 'Voice dictation for the chat input via Web Speech API.',
    author: 'g4f',
    builtin: false,
    permissions: ['dom:write', 'dom:query', 'ui:notify', 'media:record'],

    load() {
        return (async () => {
            this._recording = false;
            this._finalText = '';
            this._recognition = null;
            this._injectStyles();
            await this._injectMicButton();
            this._bindHotkey();
        })();
    },

    unload() {
        this._stop();
        document.getElementById('pa-whisperer-styles')?.remove();
        document.getElementById('pa-whisperer-btn')?.remove();
        document.getElementById('pa-whisperer-caption')?.remove();
        window.removeEventListener('keydown', this._onKeydown);
    },

    /* ---------- setup ---------- */

    _injectStyles() {
        if (document.getElementById('pa-whisperer-styles')) return;
        const style = document.createElement('style');
        style.id = 'pa-whisperer-styles';
        style.textContent = `
            #pa-whisperer-btn {
                position: absolute;
                right: 14px;
                bottom: 12px;
                z-index: 60;
                width: 38px;
                height: 38px;
                border-radius: 50%;
                border: none;
                cursor: pointer;
                font-size: 17px;
                line-height: 1;
                background: rgba(127, 127, 127, 0.18);
                color: inherit;
                transition: transform .15s ease, background .2s ease;
            }
            #pa-whisperer-btn:hover { transform: scale(1.08); }
            #pa-whisperer-btn.recording {
                background: #e5484d;
                color: #fff;
                animation: pa-whisperer-pulse 1.2s ease-in-out infinite;
            }
            @keyframes pa-whisperer-pulse {
                0%, 100% { box-shadow: 0 0 0 0 rgba(229, 72, 77, .5); }
                50%      { box-shadow: 0 0 0 9px rgba(229, 72, 77, 0); }
            }
            #pa-whisperer-caption {
                position: absolute;
                left: 14px;
                right: 64px;
                bottom: 56px;
                z-index: 60;
                max-height: 84px;
                overflow: hidden;
                padding: 8px 12px;
                border-radius: 10px;
                background: rgba(0, 0, 0, .75);
                color: #fff;
                font-size: 13px;
                line-height: 1.45;
                pointer-events: none;
                opacity: 0;
                transition: opacity .2s ease;
            }
            #pa-whisperer-caption.visible { opacity: 1; }
            #pa-whisperer-caption .interim { opacity: .6; font-style: italic; }
        `;
        document.head.appendChild(style);
    },

    _injectMicButton() {
        return new Promise((resolve) => {
            const tryInject = () => {
                const inputArea = document.querySelector(
                    '.input-area, .chat-input-area, #chat-input-area, main .bottom, .message-input-area'
                );
                if (!inputArea) {
                    setTimeout(tryInject, 300);
                    return;
                }
                if (document.getElementById('pa-whisperer-btn')) {
                    resolve();
                    return;
                }

                if (!inputArea.style.position || inputArea.style.position === 'static') {
                    inputArea.style.position = 'relative';
                }

                const btn = document.createElement('button');
                btn.id = 'pa-whisperer-btn';
                btn.type = 'button';
                btn.title = 'Voice input (Alt+M)';
                btn.textContent = '🎙️';
                btn.addEventListener('click', () => this._toggle());
                inputArea.appendChild(btn);
                this._btn = btn;

                const caption = document.createElement('div');
                caption.id = 'pa-whisperer-caption';
                inputArea.appendChild(caption);
                this._caption = caption;

                resolve();
            };
            tryInject();
        });
    },

    _bindHotkey() {
        this._onKeydown = (e) => {
            if (e.altKey && (e.key === 'm' || e.key === 'M')) {
                e.preventDefault();
                this._toggle();
            }
        };
        window.addEventListener('keydown', this._onKeydown);
    },

    /* ---------- speech recognition ---------- */

    _supported() {
        return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
    },

    _toggle() {
        this._recording ? this._stop() : this._start();
    },

    _start() {
        if (this._recording) return;

        if (!this._supported()) {
            ChatAddonHost.notify(
                'Chat Whisperer: this browser has no Web Speech API (try Chrome/Edge)',
                'error'
            );
            return;
        }

        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        const rec = new SR();
        rec.continuous = true;
        rec.interimResults = true;
        rec.lang = navigator.language || 'en-US';

        this._finalText = '';

        rec.onresult = (event) => {
            let interim = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const chunk = event.results[i][0].transcript;
                if (event.results[i].isFinal) {
                    this._finalText += chunk;
                } else {
                    interim += chunk;
                }
            }
            this._writeToInput(this._finalText + interim);
            this._showCaption(this._finalText, interim);
        };

        rec.onerror = (event) => {
            if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
                ChatAddonHost.notify('Chat Whisperer: microphone permission denied', 'error');
                this._stop();
            } else if (event.error !== 'no-speech' && event.error !== 'aborted') {
                ChatAddonHost.notify(`Chat Whisperer: ${event.error}`, 'error');
            }
        };

        // Chrome ends the session after silence — restart while still recording.
        rec.onend = () => {
            if (this._recording) {
                try { rec.start(); } catch (_) { /* already started */ }
            }
        };

        try {
            rec.start();
        } catch (err) {
            ChatAddonHost.notify('Chat Whisperer: could not start recognition', 'error');
            return;
        }

        this._recognition = rec;
        this._recording = true;
        this._btn?.classList.add('recording');
        this._btn?.setAttribute('title', 'Stop voice input (Alt+M)');
        ChatAddonHost.notify('🎙️ Listening… click the mic or press Alt+M to stop', 'success', 2200);
    },

    _stop() {
        if (!this._recording) return;
        this._recording = false;
        try { this._recognition?.stop(); } catch (_) { /* ignore */ }
        this._recognition = null;
        this._btn?.classList.remove('recording');
        this._btn?.setAttribute('title', 'Voice input (Alt+M)');
        this._hideCaption();
        const input = this._getInput();
        if (input && input.value.trim()) {
            input.focus();
            ChatAddonHost.notify('Dictation finished — ready to send', 'success', 1500);
        }
    },

    /* ---------- dom helpers ---------- */

    _getInput() {
        return document.querySelector(
            '#user-input, textarea[name="user_input"], .chat-input textarea, #userInput'
        );
    },

    _writeToInput(text) {
        const input = this._getInput();
        if (!input) return;

        const prefix = input.dataset.paWhispererOriginal || '';
        if (prefix === '' && input.value) {
            // First dictation chunk: preserve what the user had already typed.
            input.dataset.paWhispererOriginal = input.value.replace(/\s+$/, '') + ' ';
        }

        input.value = (input.dataset.paWhispererOriginal || '') + text.replace(/\s+$/, '');
        input.dispatchEvent(new Event('input', { bubbles: true }));
    },

    _showCaption(finalText, interim) {
        if (!this._caption) return;
        const tail = (finalText || '').slice(-140);
        this._caption.innerHTML =
            `<span>${this._escape(tail)}</span>` +
            (interim ? `<span class="interim"> ${this._escape(interim)}</span>` : '');
        this._caption.classList.add('visible');
    },

    _hideCaption() {
        if (!this._caption) return;
        this._caption.classList.remove('visible');
        setTimeout(() => { if (!this._recording) this._caption.innerHTML = ''; }, 200);
    },

    _escape(text) {
        return String(text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },
});
