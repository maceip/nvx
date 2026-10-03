/** Thin client for NVX's authenticated loopback MCP 2025-06-18 endpoint. */
export const PROTOCOL = "2025-06-18";
export type Arguments = Record<string, unknown>;
export type Output = (stream: "stdout" | "stderr", bytes: Uint8Array) => void;
export type RequestOptions = {output?: Output; timeoutMs?: number};
export type Operation = {id: number; result: Promise<Arguments>; cancel: () => Promise<void>};
export class Client {
  private sequence = 0;
  constructor(private url: string, private capability: string) {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1" || !parsed.port || parsed.pathname !== "/mcp" || parsed.username || parsed.password || parsed.search || parsed.hash) {
      throw new Error("NVX SDK requires its printed loopback /mcp URL");
    }
  }
  private async request(method: string, params: Arguments, id: number | undefined, output?: Output, signal?: AbortSignal): Promise<any> {
    const response = await fetch(this.url, {method: "POST", headers: {
      Authorization: `Bearer ${this.capability}`, "Content-Type": "application/json",
      Accept: "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOL
    }, body: JSON.stringify({jsonrpc: "2.0", method, params, ...(id === undefined ? {} : {id})}), signal});
    if (response.status === 202 && id === undefined) return {};
    if (!response.ok) throw new Error(`NVX endpoint returned HTTP ${response.status}`);
    let document: any;
    if (response.headers.get("Content-Type")?.startsWith("text/event-stream")) {
      if (!response.body) throw new Error("NVX response has no stream");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        pending += decoder.decode(value, {stream: true});
        if (pending.length > 3 * 2 ** 20) throw new Error("NVX response frame exceeds its bound");
        let end: number;
        while ((end = pending.indexOf("\n")) >= 0) {
          const line = pending.slice(0, end); pending = pending.slice(end + 1);
          if (!line.startsWith("data: ")) continue;
          const frame = JSON.parse(line.slice(6));
          if (frame.method === "notifications/progress" && output) {
            const data = JSON.parse(frame.params.message);
            output(data.stream, Uint8Array.from(atob(data.data), character => character.charCodeAt(0)));
          } else if (frame.id === id) document = frame;
        }
      }
      if (!document) throw new Error("NVX stream ended without its response");
    } else document = await response.json();
    if (document.error) throw new Error(document.error.message);
    return document.result;
  }
  async initialize(): Promise<Arguments> {
    const result = await this.request("initialize", {protocolVersion: PROTOCOL, capabilities: {}, clientInfo: {name: "nvx-typescript-sdk", version: "1.0"}}, ++this.sequence);
    if (result.protocolVersion !== PROTOCOL) throw new Error("NVX protocol version is unsupported by this SDK");
    await this.request("notifications/initialized", {}, undefined);
    return result;
  }
  async tools(): Promise<Arguments[]> { return (await this.request("tools/list", {}, ++this.sequence)).tools; }
  async cancel(id: number): Promise<void> { await this.request("notifications/cancelled", {requestId: id, reason: "SDK cancellation"}, undefined); }
  operation(name: string, args: Arguments, options: RequestOptions = {}): Operation {
    const id = ++this.sequence;
    const controller = new AbortController();
    const cancel = async () => { try { await this.cancel(id); } finally { controller.abort(); } };
    const timer = setTimeout(() => { void cancel().catch(() => {}); }, options.timeoutMs ?? 90000);
    const result = this.request("tools/call", {name, arguments: args, _meta: {progressToken: id}}, id, options.output, controller.signal).then(value => {
      if (value.isError) throw new Error(value.content[0].text);
      return JSON.parse(value.content[0].text) as Arguments;
    }).finally(() => clearTimeout(timer));
    return {id, result, cancel};
  }
  startRun(image: string, argv: string[], handle: string, args: Arguments = {}, options: RequestOptions = {}): Operation {
    return this.operation("nvx_run", {image, argv, handle, ...args}, options);
  }
  startExec(id: string, argv: string[], handle: string, args: Arguments = {}, options: RequestOptions = {}): Operation {
    return this.operation("nvx_exec", {id, argv, handle, ...args}, options);
  }
  run(image: string, argv: string[], handle: string, args: Arguments = {}, options: RequestOptions = {}): Promise<Arguments> {
    return this.startRun(image, argv, handle, args, options).result;
  }
  exec(id: string, argv: string[], handle: string, args: Arguments = {}, options: RequestOptions = {}): Promise<Arguments> {
    return this.startExec(id, argv, handle, args, options).result;
  }
}
