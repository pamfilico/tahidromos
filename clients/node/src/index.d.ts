/** Client and Playwright helpers for tahidromos. */

export interface WaitOptions {
  subjectContains?: string;
  fromContains?: string;
  toContains?: string;
  textContains?: string;
  linkContains?: string;
  hasCode?: boolean;
  messageId?: string;
  inReplyTo?: string;
  threadRoot?: string;
  unseenOnly?: boolean;
  markSeen?: boolean;
  mailbox?: string;
  /** Seconds. Defaults to 30. */
  timeout?: number;
}

export declare class Message {
  constructor(data: Record<string, unknown>);
  readonly data: Record<string, any>;
  readonly uid: string;
  readonly subject: string;
  readonly from: string;
  readonly to: string[];
  readonly cc: string[];
  readonly text: string;
  readonly html: string | null;
  /** The body without the quoted thread or signature beneath it. */
  readonly strippedText: string;
  readonly links: string[];
  /** The first link — usually the magic link or confirmation URL. */
  readonly link: string | null;
  /** The one-time code, if the message has one. */
  readonly code: string | null;
  readonly messageId: string;
  readonly inReplyTo: string | null;
  readonly references: string[];
  readonly threadRoot: string;
  readonly depth: number;
  readonly seen: boolean;
  readonly raw: string;
  linkContaining(needle: string): string;
}

export declare class Inbox {
  readonly address: string;
  readonly password: string;
  waitFor(options?: WaitOptions): Promise<Message>;
  waitForCode(options?: WaitOptions): Promise<string | null>;
  waitForLink(options?: WaitOptions): Promise<string>;
  messages(options?: { mailbox?: string; unseen?: boolean; limit?: number }): Promise<Message[]>;
  message(uid: string, mailbox?: string): Promise<Message>;
  threads(): Promise<any[]>;
  send(to: string | string[], subject?: string, text?: string, extra?: object): Promise<any>;
  reply(message: Message | string, text: string, extra?: object): Promise<any>;
  markRead(message: Message | string, seen?: boolean): Promise<any>;
  purge(mailbox?: string): Promise<number>;
  delete(): Promise<boolean>;
}

export declare class Tahidromos {
  constructor(options?: { url?: string; runId?: string });
  readonly url: string;
  readonly runId: string;
  health(): Promise<any>;
  isUp(): Promise<boolean>;
  waitUntilReady(timeoutMs?: number): Promise<void>;
  overview(): Promise<any>;
  apps(): Promise<any>;
  inbox(prefix?: string, domain?: string): Promise<Inbox>;
  mailbox(address: string, password?: string): Inbox;
  cleanup(): Promise<string[]>;
  echoBot(): Promise<string | null>;
  send(from: string, to: string | string[], subject?: string, text?: string, extra?: object): Promise<any>;
  template(name: string, to: string, context?: object): Promise<any>;
  templates(): Promise<any[]>;
  scenario(name: string, to: string, options?: object): Promise<any>;
  scenarios(): Promise<any[]>;
  conversation(participants: string[], options?: object): Promise<any>;
  spam(payload: object): Promise<any>;
  parse(text?: string, html?: string): Promise<any>;
}

export declare class TahidromosError extends Error {}
export declare class MessageNotFound extends TahidromosError {}
export default Tahidromos;
