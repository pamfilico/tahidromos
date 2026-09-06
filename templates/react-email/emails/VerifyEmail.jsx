import {
  Body, Button, Container, Head, Html, Preview, Section, Text, Heading,
} from "@react-email/components";
import * as React from "react";

/** Address confirmation — the other half of the sign-up flow. */
export default function VerifyEmail() {
  return (
    <Html lang="en">
      <Head />
      <Preview>{"Confirm {{ email }} to finish setting up {{ product }}"}</Preview>
      <Body style={body}>
        <Container style={card}>
          <Section style={pad}>
            <Text style={brand}>📮 {"{{ product }}"}</Text>
            <Heading style={heading}>Confirm your address</Heading>
            <Text style={paragraph}>
              One click and {"{{ email }}"} is verified. The link is good for{" "}
              {"{{ expires_hours }}"} hours.
            </Text>
            <Button href={"{{ verify_url }}"} style={button}>
              Confirm {"{{ email }}"}
            </Button>
            <Text style={mono}>{"{{ verify_url }}"}</Text>
            <Text style={small}>
              If you did not create an account, ignore this — nothing happens
              until the link is used.
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
const body = { margin: 0, padding: "32px 12px", backgroundColor: "#f4f5f7", fontFamily: fontStack };
const card = {
  maxWidth: "600px", margin: "0 auto", backgroundColor: "#ffffff",
  borderRadius: "12px", border: "1px solid #e5e7eb",
};
const pad = { padding: "26px 32px 30px" };
const brand = { fontSize: "15px", fontWeight: 600, color: "#111827", margin: 0 };
const heading = {
  fontSize: "24px", fontWeight: 600, color: "#111827",
  letterSpacing: "-0.02em", margin: "20px 0 10px",
};
const paragraph = { fontSize: "15px", lineHeight: 1.6, color: "#4b5563", margin: "0 0 20px" };
const button = {
  backgroundColor: "#111827", borderRadius: "8px", color: "#ffffff",
  fontSize: "15px", fontWeight: 600, textDecoration: "none",
  padding: "12px 22px", display: "inline-block",
};
const mono = {
  fontSize: "12px", color: "#2563eb", wordBreak: "break-all",
  fontFamily: "ui-monospace,SFMono-Regular,Menlo,monospace", margin: "18px 0 0",
};
const small = { fontSize: "13px", lineHeight: 1.6, color: "#6b7280", margin: "18px 0 0" };
const footer = { fontSize: "12px", color: "#9ca3af", textAlign: "center", margin: "18px 0 0" };
