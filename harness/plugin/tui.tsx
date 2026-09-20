// Harness CLI plugin for the opencode TUI. Lives beside server.ts in
// ~/.config/opencode/plugins/harness/ — the loader probes a plugin DIRECTORY
// for `server.*` and `tui.*` entrypoints; a bare harness.ts can only ever be a
// server plugin, which is exactly why it used to show under "Server" in the
// TUI's Plugins panel while never appearing in the plugin list (that list
// filters on features.tui, which is only set when a tui entrypoint exists).
//
// Contract (opencode v2.0.8):
//   * The TUI compiles this .tsx itself (JSX -> Solid/OpenTUI elements), so
//     JSX needs no imports here. The loader also resolves the
//     `@opencode/plugin/tui` specifier for CLI plugins, but setup() receives
//     the full context, so nothing needs importing — keeping the file
//     import-free like harness/plugin/harness.ts.
//   * Default export: { id, setup }. setup(ctx) gets the TUI context
//     (app, client, data, theme, renderer, ui, ...). ctx.ui.slot({placement,
//     target, render}) adds JSX at a named slot. Placements:
//     prepend|append|before|after|replace. Targets: app, home.footer,
//     home.footer.status, prompt.footer, prompt.footer.status,
//     prompt.footer.file, session.composer.top, sidebar.content,
//     sidebar.footer, session.panel.
//   * `home.footer.status` appends into the built-in footer row after
//     opencode's health indicators — a small, non-intrusive "harness" chip
//     that also makes the TUI list this plugin (features.tui).
//   * Every step is guarded: a TUI API drift must degrade to a log line, not
//     break the terminal.

type Rec = Record<string, any>

const log = (m: string) => console.error(`[harness tui] ${m}`)

const HarnessTui = {
  id: "harness",

  setup(ctx: Rec) {
    try {
      if (!ctx?.ui || typeof ctx.ui.slot !== "function") {
        log("ui.slot unavailable on this opencode version — TUI chip skipped")
        return
      }
      ctx.ui.slot({
        append: "home.footer.status",
        render: ({ sessionID }: Rec = {}) => {
          try {
            const fg = ctx?.theme?.text?.muted ?? ctx?.theme?.text?.base ?? "magenta"
            return (
              <text fg={fg}>harness</text>
            )
          } catch (e) {
            log(`render failed: ${e instanceof Error ? e.message : String(e)}`)
            return null
          }
        },
      })
    } catch (e) {
      log(`slot registration failed: ${e instanceof Error ? e.message : String(e)}`)
    }
  },
}

export default HarnessTui
