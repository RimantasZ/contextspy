export const CONTENT_FIXTURES = {
  json: '{"meta":{"enabled":true,"items":[1,2]}}',
  xml: '<root><empty/><nested><leaf/></nested></root>',
  mixedXml: '<root>Hello <strong>world</strong>.</root>',
  toml: '[server]\nport=8080\nenabled=true',
  javascript: 'const pattern = /a{2,3}/;\nconst template = `value ${pattern}`;',
  python: 'def run():\n    message = """keep\n    spacing"""\n    return message',
  yaml: 'service:\n  enabled: true',
  unicode: 'Hello 👋 世界 — café',
  large: 'A'.repeat(100_000),
  empty: '',
} as const
