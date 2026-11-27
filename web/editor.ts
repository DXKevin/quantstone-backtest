import {basicSetup, EditorView} from "codemirror";
import {EditorState, Prec} from "@codemirror/state";
import {keymap} from "@codemirror/view";
import {indentWithTab} from "@codemirror/commands";
import {autocompletion, snippetCompletion, type CompletionContext} from "@codemirror/autocomplete";
import {python} from "@codemirror/lang-python";
import {oneDark} from "@codemirror/theme-one-dark";

declare global {
  interface Window {
    quantstoneEditor?: {
      getValue(): string;
      setValue(value: string): void;
      focus(): void;
    };
  }
}

const textarea = document.getElementById("strategy") as HTMLTextAreaElement | null;
const parent = document.getElementById("strategy-editor") as HTMLElement | null;

if (textarea && parent) {
  const callbacks = [
    snippetCompletion("def init(context):\n    ${}", {label: "init", type: "function", detail: "初始化策略"}),
    snippetCompletion("def before_trading(context):\n    ${}", {label: "before_trading", type: "function", detail: "盘前回调"}),
    snippetCompletion("def handle_bar(context):\n    ${}", {label: "handle_bar", type: "function", detail: "行情回调"}),
    snippetCompletion("def after_trading(context):\n    ${}", {label: "after_trading", type: "function", detail: "盘后回调"}),
  ];
  const contextMethods = [
    snippetCompletion("current(${symbol})", {label: "current", type: "method", detail: "最近已完成行情"}),
    snippetCompletion("history(${symbol}, ${count}, ${frequency})", {label: "history", type: "method", detail: "历史行情"}),
    snippetCompletion("order(${symbol}, ${side}, ${quantity})", {label: "order", type: "method", detail: "提交委托"}),
    snippetCompletion("order_all(${symbol}, ${side})", {label: "order_all", type: "method", detail: "全仓买入或清仓"}),
    snippetCompletion("cancel_order(${order_id})", {label: "cancel_order", type: "method", detail: "撤销委托"}),
    snippetCompletion("set_fees(${})", {label: "set_fees", type: "method", detail: "设置交易费用"}),
    {label: "portfolio", type: "property", detail: "账户与持仓"},
    {label: "state", type: "property", detail: "策略状态"},
    snippetCompletion("log(${message})", {label: "log", type: "method", detail: "记录策略日志"}),
  ];

  function quantstoneCompletion(context: CompletionContext) {
    const word = context.matchBefore(/[\w.]*/);
    if (!word || (word.from === word.to && !context.explicit)) return null;
    const dot = word.text.lastIndexOf(".");
    if (dot >= 0 && ["context", "ctx"].includes(word.text.slice(0, dot))) {
      return {from: word.from + dot + 1, options: contextMethods, validFor: /^\w*$/};
    }
    return {from: word.from, options: callbacks, validFor: /^\w*$/};
  }

  const shortcuts = Prec.highest(keymap.of([
    {key: "Mod-s", run: () => {document.dispatchEvent(new CustomEvent("quantstone-save")); return true;}},
    {key: "Mod-Enter", run: () => {document.dispatchEvent(new CustomEvent("quantstone-run")); return true;}},
    indentWithTab,
  ]));
  const terminalTheme = EditorView.theme({
    "&": {height: "100%", backgroundColor: "#20252e", fontSize: "14px"},
    ".cm-scroller": {fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace', lineHeight: "1.65"},
    ".cm-content": {padding: "16px 0", caretColor: "#dbe8ff"},
    ".cm-line": {padding: "0 18px"},
    ".cm-gutters": {backgroundColor: "#1c2129", color: "#697586", border: "none", paddingLeft: "8px"},
    ".cm-activeLineGutter": {backgroundColor: "#282e38", color: "#c4cedc"},
    ".cm-activeLine": {backgroundColor: "#ffffff08"},
    ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {backgroundColor: "#315a8a80"},
    "&.cm-focused": {outline: "none"},
  }, {dark: true});

  let silent = false;
  const syncTextarea = EditorView.updateListener.of(update => {
    if (!update.docChanged) return;
    textarea.value = update.state.doc.toString();
    if (!silent) textarea.dispatchEvent(new Event("input", {bubbles: true}));
  });
  const view = new EditorView({
    state: EditorState.create({
      doc: textarea.value,
      extensions: [basicSetup, python(), oneDark, terminalTheme, shortcuts,
        autocompletion({override: [quantstoneCompletion], activateOnTyping: true}), syncTextarea],
    }),
    parent,
  });

  window.quantstoneEditor = {
    getValue: () => view.state.doc.toString(),
    setValue(value) {
      if (value === view.state.doc.toString()) return;
      silent = true;
      view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: value}});
      silent = false;
      textarea.value = value;
    },
    focus: () => view.focus(),
  };
  document.dispatchEvent(new CustomEvent("quantstone-editor-ready"));
}
