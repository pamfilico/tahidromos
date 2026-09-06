These templates are written the way production email has to be written:

* one 600px table, because float and flex are unreliable in Outlook
* every style inline, because `<style>` is stripped by several clients
* a preheader span, so the inbox preview is not the first link
* `prefers-color-scheme` for the clients that honour it, with colours that
  still read correctly in the ones that do not
* a plain-text alternative for every one

`{{ variable }}` is substituted and HTML-escaped. `{{{ variable }}}` is
substituted raw. `{{#each items}} … {{/each}}` repeats a block.
