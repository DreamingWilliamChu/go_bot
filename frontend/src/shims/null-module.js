// @sabaki/sgf optionally requires iconv-lite/jschardet for decoding raw byte buffers. The app only
// parses strings, so these Node-oriented packages are replaced by null, which makes @sabaki/sgf
// use its built-in UTF-8 fallback (see node_modules/@sabaki/sgf/src/iconv-lite.js).
export default null
