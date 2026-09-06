import {
  Body, Button, Container, Head, Hr, Html, Img, Preview,
  Section, Text, Heading, Link, Row, Column,
} from "@react-email/components";
import { LOGO, markStyle } from "./brand.js";
import * as React from "react";

/**
 * A team invitation. Authored here, exported to plain HTML that the mail
 * server ships — so the same markup your app would send is the markup you
 * test against.
 *
 * `{{ handlebars }}` placeholders survive the export untouched, which is how
 * a React component becomes a server-side template.
 */
export default function Invite() {
  return (
    <Html lang="en">
      <Head />
      <Preview>{"{{ inviter }} added you to {{ team }} on {{ product }}"}</Preview>
      <Body style={body}>
        <Container style={card}>
          <Section style={pad}>
            <Text style={brand}>
              <Img src={LOGO} alt="" width="26" height="26" style={markStyle} />
              {"{{ product }}"}
            </Text>
          </Section>

          <Section style={pad}>
            <Heading style={heading}>
              {"{{ inviter }}"} added you to {"{{ team }}"}
            </Heading>
            <Text style={paragraph}>
              You now have {"{{ role }}"} access. Nothing to set up — open the
              workspace and you are in.
            </Text>
            <Button href={"{{ accept_url }}"} style={button}>
              Open {"{{ team }}"}
            </Button>
          </Section>

          <Section style={pad}>
            <Row style={factRow}>
              <Column style={factLabel}>Workspace</Column>
              <Column style={factValue}>{"{{ team }}"}</Column>
            </Row>
            <Row style={factRow}>
              <Column style={factLabel}>Role</Column>
              <Column style={factValue}>{"{{ role }}"}</Column>
            </Row>
            <Row style={factRow}>
              <Column style={factLabel}>Invited by</Column>
              <Column style={factValue}>{"{{ inviter_email }}"}</Column>
            </Row>
          </Section>

          <Hr style={rule} />
          <Section style={pad}>
            <Text style={small}>
              This invitation expires in {"{{ expires_days }}"} days. Not expecting
              it? <Link href={"{{ decline_url }}"} style={link}>Decline</Link>.
            </Text>
          </Section>
        </Container>
        <Text style={footer}>{"{{ company }}"}</Text>
      </Body>
    </Html>
  );
}

const fontStack =
  '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif';
const monoStack = 'ui-monospace,SFMono-Regular,Menlo,monospace';

const body = { margin: 0, padding: "32px 12px", backgroundColor: "#f4f5f7", fontFamily: fontStack };
const card = {
  maxWidth: "600px", margin: "0 auto", backgroundColor: "#ffffff",
  borderRadius: "12px", border: "1px solid #e5e7eb",
};
const pad = { padding: "0 32px" };
const brand = { fontSize: "15px", fontWeight: 600, color: "#111827", margin: "26px 0 0" };
const heading = {
  fontSize: "24px", fontWeight: 600, color: "#111827",
  letterSpacing: "-0.02em", lineHeight: 1.25, margin: "20px 0 10px",
};
const paragraph = { fontSize: "15px", lineHeight: 1.6, color: "#4b5563", margin: "0 0 20px" };
const button = {
  backgroundColor: "#2563eb", borderRadius: "8px", color: "#ffffff",
  fontSize: "15px", fontWeight: 600, textDecoration: "none",
  padding: "12px 22px", display: "inline-block",
};
const factRow = { borderBottom: "1px solid #f3f4f6" };
const factLabel = { fontSize: "13px", color: "#6b7280", padding: "10px 0", width: "120px" };
const factValue = { fontSize: "13px", color: "#111827", padding: "10px 0", fontFamily: monoStack };
const rule = { borderColor: "#e5e7eb", margin: "24px 0 0" };
const small = { fontSize: "13px", lineHeight: 1.6, color: "#6b7280", margin: "18px 0 30px" };
const link = { color: "#2563eb" };
const footer = { fontSize: "12px", color: "#9ca3af", textAlign: "center", margin: "18px 0 0" };
