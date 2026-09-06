/**
 * The mark, as a handlebars placeholder.
 *
 * The mail server substitutes `{{ logo }}` with the inlined postman at send
 * time, so the exported HTML stays small and the image still travels with
 * every message.
 */
export const LOGO = "{{ logo }}";

export const markStyle = {
  display: "inline-block",
  verticalAlign: "middle",
  borderRadius: "50%",
  marginRight: "9px",
  width: "26px",
  height: "26px",
};
