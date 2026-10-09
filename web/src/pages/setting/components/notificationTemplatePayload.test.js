import assert from 'node:assert/strict'
import test from 'node:test'
import { buildNotificationTemplateSavePayload, buildNotificationTemplatePreviewPayload } from './notificationTemplatePayload.js'
import zhCN from '../../../i18n/locales/zh-CN.js'
import en from '../../../i18n/locales/en.js'
import zhTW from '../../../i18n/locales/zh-TW.js'

test('任务进度保存自定义标题和 Jinja 进度条，不启用图片', () => {
  const values = {
    title: '{{ task_title }} #{{ task_id }}',
    body: "{{ '#' * (progress // 10) }}{{ '-' * (10 - progress // 10) }} {{ progress }}%\n{{ description }}",
    imageEnabled: true,
  }
  assert.deepEqual(buildNotificationTemplateSavePayload('task_progress', values), {
    title: values.title, body: values.body, imageEnabled: false,
  })
  for (const progress of [0, 50, 100]) {
    const payload = buildNotificationTemplatePreviewPayload('task_progress', values, 'telegram', 'success', progress)
    assert.deepEqual(payload, {
      templateId: 'task_progress', title: values.title, body: values.body,
      imageEnabled: false, channel: 'telegram', exampleStatus: 'running', exampleProgress: progress,
    })
  }
})

test('完成通知保留已编辑正文、图片开关及原有示例状态', () => {
  for (const imageEnabled of [true, false]) {
    const values = { title: '已编辑标题', body: '已编辑正文 {{ message }}', imageEnabled }
    assert.deepEqual(buildNotificationTemplateSavePayload('danmaku_import', values), values)
    assert.deepEqual(buildNotificationTemplatePreviewPayload('danmaku_import', values, 'webhook', 'failed'), {
      templateId: 'danmaku_import', ...values, channel: 'webhook', exampleStatus: 'failed',
    })
  }
})

test('任务进度新增文案和变量三语完整', () => {
  const keys = ['taskProgressName', 'taskProgressDescription', 'progressLabel', 'statusRunning', 'imageLabel', 'imageDescription', 'imageOn', 'imageOff', 'channelPlaceholder', 'exampleImage']
  for (const locale of [zhCN, en, zhTW]) {
    for (const key of keys) assert.ok(locale.notificationTemplate[key], key)
    for (const variable of ['task_title', 'task_id', 'progress', 'progress_bar', 'description']) {
      assert.ok(locale.notificationTemplate.taskProgressVariables[variable], variable)
    }
  }
})
