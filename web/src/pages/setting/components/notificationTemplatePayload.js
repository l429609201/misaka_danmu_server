/** 构造保存数据，任务进度始终使用纯文字，避免影响完成通知配置。 */
export const buildNotificationTemplateSavePayload = (templateId, values) => ({
  title: values.title,
  body: values.body,
  imageEnabled: templateId !== 'task_progress' && values.imageEnabled !== false,
})

/** 预览沿用保存正文和后端渲染器，仅任务进度附加百分比示例。 */
export const buildNotificationTemplatePreviewPayload = (templateId, values, channel, status, progress = 50) => ({
  templateId,
  ...buildNotificationTemplateSavePayload(templateId, values),
  channel,
  exampleStatus: templateId === 'task_progress' ? 'running' : status,
  ...(templateId === 'task_progress' ? { exampleProgress: progress } : {}),
})
