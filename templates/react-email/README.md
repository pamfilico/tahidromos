# Authoring templates in React Email

The mail server renders plain HTML files. This directory is the optional
authoring path: write a template as a React component, export it to HTML, and
the server serves it like any other.

```sh
cd templates/react-email
npm install
npm run dev       # http://localhost:3030 — live preview while you edit
npm run export    # write the HTML into ../../tahidromos/templates/
npm run check     # fail if the committed HTML is stale (use this in CI)
```

## How a component becomes a server-side template

The components keep `{{ handlebars }}` placeholders in their text:

```jsx
<Heading>{"{{ inviter }}"} added you to {"{{ team }}"}</Heading>
```

React Email renders that to HTML with the placeholders intact, and the mail
server substitutes them at send time. So React Email is the design tool and
the server does the data binding — you get the live preview and the component
model without pulling Node into the server.

## Adding one

1. Write `emails/YourEmail.jsx`.
2. Add an entry to `META` in `export.jsx`: the subject, the From address, and
   the default values. The key is the snake_case form of the filename, so
   `YourEmail.jsx` becomes `your_email`.
3. `npm run export`.

The export rewrites `tahidromos/templates/manifest.json`, preserving the
handwritten entries and replacing the `react-email` ones.

## Which to use

| | Handwritten HTML | React Email |
| --- | --- | --- |
| Edit and reload | instant | needs an export |
| Node required | no | yes, to author |
| Component reuse | copy and paste | real components |
| Live preview | via tahidromos | `npm run dev` |

Both end up as the same thing: an HTML file plus a manifest entry. Neither is
required at runtime — the server only ever reads the exported HTML.
