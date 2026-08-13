# pa-coding – The Self-Developing MCP Agent in gpt4free

`pa-coding` is a coding agent for [gpt4free](https://github.com/xtekky/gpt4free) that can be activated directly inside the g4f web UI through the Addon Manager. It reuses the built-in MCP tool stack to write, refactor, debug, and explain code, and it can apply changes to the gpt4free `/chat/` v2 interface directly from the workspace—online after a page reload.

## ✨ Features

- **Self-developing agent:** Writes, debugs, refactors, and explains code using your MCP tools (filesystem, git, search, browser debugging).
- **Addon Manager integration:** Activate it from the g4f Addon Manager; no manual setup required.
- **Direct prompt input:** Send prompts directly from the web UI; the agent executes them and the changes to `/chat/` v2 are live after reloading the page.
- **Simple configuration:** Instead of a complex setup, you can simply enter the local address of your Ollama server or any other PA provider as the Base URL.
- **Browser debugging:** Supports browser error debugging. Just enable the corresponding browser addon (for example `workspace:browser-errors`) so the agent can access console errors.

## 🚀 Activation

1. Open the **Addon Manager** in your g4f installation.
2. Look for **`workspace:pa-coding`** or place the file `pa-coding.js` into your workspace directory (`~/.g4f/workspace/pa-providers/`).
3. Enable the addon.
4. Reload the page.

## 🎯 Usage

### Send a prompt
- After activation, the **Coding Agent** panel appears in the chat.
- Enter your prompt directly into the input field and press **Enter** or click the send button.
- The agent uses your configured MCP tools and returns the result directly in the chat.

### Changes to `/chat/` v2
- All files created or modified by the agent (for example edits to the g4f web UI `/chat/` v2) are saved directly in the workspace.
- After **reloading the page**, those changes are online and visible immediately.

### Configure Ollama / PA provider
- Instead of a complex model setup, simply enter the **Base URL** of your local Ollama server (or any other OpenAI-compatible PA provider) in the panel, for example:
  ```
  http://localhost:11434/v1
  ```
- Choose the desired model, or leave it empty to use the model selected in the main chat.

### Enable browser debugging
- To send browser errors to the agent, enable the addon **`workspace:browser-errors`** (or a similar debugging addon).
- In the Coding Agent panel, click the **🐛** button to send captured browser errors as context.

## 📁 Technical details

- **Addon ID:** `workspace:pa-coding`
- **Version:** `1.0.4`
- **Permissions:** `dom:read`, `dom:write`, `storage:local`, `ui:notify`, `net:fetch`
- **Supported tools:** filesystem, git, search, browser debugging, code execution

## 📄 License

Part of [gpt4free](https://github.com/xtekky/gpt4free) / PA Providers.
