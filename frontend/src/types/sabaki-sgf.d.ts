declare module '@sabaki/sgf' {
  export interface SgfNode {
    id: number | string | null
    data: Record<string, string[]>
    parentId: number | string | null
    children: SgfNode[]
  }
  export function parse(contents: string, options?: Record<string, unknown>): SgfNode[]
  export function stringify(nodes: SgfNode | SgfNode[], options?: { linebreak?: string; indent?: string }): string
  export function escapeString(s: string): string
  export function unescapeString(s: string): string
  const _default: {
    parse: typeof parse
    stringify: typeof stringify
  }
  export default _default
}
