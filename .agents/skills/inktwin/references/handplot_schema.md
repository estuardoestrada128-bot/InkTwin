# Handplot Declarative Sketch Schema

Embed fenced `handplot` code blocks inside Markdown to render zero-font, pressure-tapered sketches:

```markdown
\`\`\`handplot
type: number_line | func | fbd
width_mm: 96
height_mm: 38
caption: "Sketch description"
\`\`\`
```

## Supported Types

- **`number_line`**: `range: [xmin, xmax]`, `ticks: [...]`, `label: "x"`, `intervals: [{from, to, open_left, open_right, color}]`.
- **`func`**: `x_range: [xmin, xmax]`, `y_range: [ymin, ymax]`, `shade: {expr, from, to}`, `curves: [{expr, domain, color}]`, `points: [{x, y, label}]`. Safe math AST supports `+ - * / ** sin cos tan asin acos atan exp log sqrt abs pi e`.
- **`fbd`**: `body: "box" | "circle" | "wedge"`, `angle_deg`, `forces: [{dir_deg, label, mag, color}]`.

