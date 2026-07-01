import { Card, Button, List, Tag, Space, Input, Collapse, Spin, Alert, message } from 'antd'
import { useTranslation } from 'react-i18next'
import { useState } from 'react'
import { checkRecognitionConflicts, testRecognitionRule } from '@/apis'
import { AuditOutlined, SearchOutlined, WarningOutlined } from '@ant-design/icons'

const issueLabels = {
  empty: '空规则', duplicate: '重复', too_short: '关键词过短',
  overlap: '范围重叠', unreachable: '不可达', parse_error: '解析失败',
}
const issueSeverity = { warning: 'orange', error: 'red', info: 'blue' }

export const RecognitionCheckPanel = () => {
  const { t } = useTranslation()
  const [conflicts, setConflicts] = useState([])
  const [loading, setLoading] = useState(false)
  const [testTitle, setTestTitle] = useState('')
  const [testResult, setTestResult] = useState(null)
  const [testLoading, setTestLoading] = useState(false)

  const doCheck = () => {
    setLoading(true)
    checkRecognitionConflicts().then(res => { const d = res?.data; setConflicts(Array.isArray(d) ? d : []); setLoading(false) }).catch(() => setLoading(false))
  }

  const doTest = () => {
    if (!testTitle.trim()) return
    setTestLoading(true)
    testRecognitionRule({ title: testTitle }).then(res => { setTestResult(res?.data); setTestLoading(false) }).catch(() => setTestLoading(false))
  }

  return (
    <div className="space-y-4">
      <Card title={<><AuditOutlined className="mr-2" />{t('recognitionCheck.conflictTitle')}</>} size="small"
        extra={<Button onClick={doCheck} loading={loading} size="small">{t('recognitionCheck.scan')}</Button>}>
        {loading ? <Spin className="flex justify-center py-4" /> : conflicts.length === 0 ? (
          <Alert type="success" message={t('recognitionCheck.noConflict')} showIcon />
        ) : (
          <List size="small" dataSource={conflicts} renderItem={item => (
            <List.Item>
              <Space direction="vertical" className="w-full">
                <Space>
                  <Tag color={issueSeverity[item.severity]}>{issueLabels[item.issueType] || item.issueType}</Tag>
                  <span className="text-xs text-gray-400">#{item.ruleIndex + 1}</span>
                </Space>
                <div className="text-xs font-mono bg-gray-50 dark:bg-gray-800 p-1 rounded">{item.ruleContent}</div>
                <div className="text-xs text-gray-500">{item.detail}</div>
              </Space>
            </List.Item>
          )} />
        )}
      </Card>

      <Card title={<><SearchOutlined className="mr-2" />{t('recognitionCheck.testTitle')}</>} size="small">
        <Space.Compact className="w-full mb-3">
          <Input placeholder={t('recognitionCheck.testPlaceholder')} value={testTitle} onChange={e => setTestTitle(e.target.value)} onPressEnter={doTest} />
          <Button type="primary" onClick={doTest} loading={testLoading}>{t('recognitionCheck.test')}</Button>
        </Space.Compact>
        {testResult && (
          <div className="space-y-2">
            <div className="text-sm"><span className="text-gray-500">{t('recognitionCheck.original')}:</span> {testResult.originalTitle}</div>
            <div className="text-sm"><span className="text-gray-500">{t('recognitionCheck.result')}:</span> <Tag color="green">{testResult.transformedTitle}</Tag></div>
            {testResult.matchedRules?.length > 0 && (
              <Collapse size="small" items={[{
                key: '1',
                label: `${t('recognitionCheck.hitRules')} (${testResult.matchedRules.length})`,
                children: testResult.matchedRules.map((r, i) => (
                  <div key={i} className="text-xs mb-1">
                    <Tag>#{r.ruleIndex + 1}</Tag>
                    <span className="font-mono">{r.rule}</span>
                    <span className="ml-2 text-gray-400">{r.before} → {r.after}</span>
                  </div>
                )),
              }]} />
            )}
          </div>
        )}
      </Card>
    </div>
  )
}
