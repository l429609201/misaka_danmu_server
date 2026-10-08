/** 判断文件 basename 是否包含禁读的 .so 标记，防止版本后缀和扩展名伪装。 */
export function isSharedLibraryFileName(fileName) {
  if (typeof fileName !== 'string') return false
  const basename = fileName.split(/[\\/]/).at(-1)
  return basename.toLowerCase().includes('.so')
}
