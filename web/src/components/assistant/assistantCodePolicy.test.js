import assert from 'node:assert/strict'
import test from 'node:test'
import { isSharedLibraryFileName } from './assistantCodePolicy.js'

test('拒绝 basename 任意位置的 .so 标记，不区分大小写', () => {
  for (const name of ['lib.so', 'lib.SO', 'lib.so.1', 'lib.so.py', 'photo.so.png', '.so', 'a.So.backup', '/tmp/lib.so.2', 'C:\\tmp\\lib.SO.py']) {
    assert.equal(isSharedLibraryFileName(name), true, name)
  }
})

test('只检查 basename，正常代码及图片不被目录名误伤', () => {
  for (const name of ['app.py', 'image.png', 'source.js', 'so.py', 'libso', '/tmp/cache.so/app.py', 'C:\\cache.so\\image.png', '']) {
    assert.equal(isSharedLibraryFileName(name), false, name)
  }
})

test('缺失或非字符串文件名不会触发读取或异常', () => {
  for (const name of [undefined, null, 123, {}]) {
    assert.equal(isSharedLibraryFileName(name), false)
  }
})
