/* ================================================================== *
 * Addon: Chat Quick Actions
 *
 * Adds a toolbar of one-click prompt actions above the chat input.
 * ================================================================== */

ChatAddons.register({
    id: 'workspace:chat-quick-actions',
    name: 'Chat Quick Actions',
    version: '1.0.0',
    description: 'One-click prompt actions toolbar for /chat/.',
    author: 'g4f',
    builtin: false,
    permissions: ['dom:write', 'dom:query', 'ui:notify'],

    load() {
        return (async () => {
            await this._injectToolbar();
            ChatAddonHost.onMessageRender('workspace:chat-quick-actions', (ctx) => {
                this._maybeAnimateSend(ctx);
            });
        })();
    },

    unload() {
        const toolbar = document.getElementById('pa-quick-actions-toolbar');
        if (toolbar) toolbar.remove();
    },

    _injectToolbar() {
        return new Promise((resolve) => {
            const tryInject = () => {
                const inputArea = document.querySelector('.input-area, .chat-input-area, #chat-input-area, main .bottom, .message-input-area');
                if (!inputArea) {
                    setTimeout(tryInject, 300);
                    return;
                }

                if (document.getElementById('pa-quick-actions-toolbar')) {
                    resolve();
                    return;
                }

                const toolbar = document.createElement('div');
                toolbar.id = 'pa-quick-actions-toolbar';
                toolbar.className = 'pa-quick-actions';
                toolbar.innerHTML = `
                    <div class="pa-quick-actions-inner">
                        <span class="pa-quick-actions-label">Quick Actions</span>
                        <div class="pa-quick-actions-buttons">
                            <button data-prompt="Summarize the previous response in 3 bullet points.">Summarize</button>
                            <button data-prompt="Rewrite the previous response to be more concise.">Shorten</button>
                            <button data-prompt="Explain the previous response like I am 12 years old.">Explain</button>
                            <button data-prompt="List 3 follow-up questions about the previous response.">Follow-ups</button>
                            <button data-prompt="Translate the previous response to English.">Translate</button>
                            <button data-prompt="Fact-check the previous response and mark uncertain claims.">Fact-check</button>
                        </div>
                    </div>
                `;

                inputArea.parentNode.insertBefore(toolbar, inputArea);

                toolbar.querySelectorAll('button[data-prompt]').forEach((btn) => {
                    btn.addEventListener('click', () => {
                        const prompt = btn.getAttribute('data-prompt');
                        const userInput = document.querySelector('#user-input, textarea[name="user_input"], .chat-input textarea, #userInput');
                        if (userInput) {
                            userInput.value = prompt;
                            userInput.dispatchEvent(new Event('input', { bubbles: true }));
                            userInput.focus();
                            ChatAddonHost.notify('Quick action inserted', 'success', 1500);
                        } else {
                            ChatAddonHost.notify('Could not find chat input', 'error');
                        }
                    });
                });

                resolve();
            };

            tryInject();
        });
    },

    _maybeAnimateSend(ctx) {
        const sendButton = document.querySelector('#send-button, .send-button, button[aria-label="Send"]');
        if (!sendButton || !ctx?.message?.content) return;

        const text = String(ctx.message.content).toLowerCase();
        const markers = ['summarize', 'shorten', 'explain', 'follow-up', 'translate', 'fact-check'];
        if (markers.some((m) => text.includes(m))) {
            sendButton.classList.add('pa-quick-actions-highlight');
            setTimeout(() => sendButton.classList.remove('pa-quick-actions-highlight'), 1200);
        }
    },
});
