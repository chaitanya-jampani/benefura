export interface Part {
  name: string;
  filename?: string;
  contentType?: string;
  data: Buffer;
}

export function parseMultipart(body: Buffer, contentType: string): Part[] {
  const boundary = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType);
  if (!boundary) throw new Error(`No multipart boundary in ${contentType}`);
  const delimiter = Buffer.from(`--${boundary[1] ?? boundary[2]}`);
  const parts: Part[] = [];
  let start = body.indexOf(delimiter);
  while (start >= 0) {
    const next = body.indexOf(delimiter, start + delimiter.length);
    if (next < 0) break;
    const chunk = body.subarray(start + delimiter.length + 2, next - 2); // skip CRLF after delimiter and before next
    const headerEnd = chunk.indexOf("\r\n\r\n");
    if (headerEnd >= 0) {
      const headers = chunk.subarray(0, headerEnd).toString("latin1");
      const disposition = /content-disposition:[^\r\n]*/i.exec(headers)?.[0] ?? "";
      const name = /name="([^"]*)"/i.exec(disposition)?.[1] ?? "";
      const filename = /filename="([^"]*)"/i.exec(disposition)?.[1];
      const type = /content-type:\s*([^\r\n]+)/i.exec(headers)?.[1];
      parts.push({ name, filename, contentType: type, data: Buffer.from(chunk.subarray(headerEnd + 4)) });
    }
    start = next;
  }
  return parts;
}
