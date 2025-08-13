import { getMatchTest } from '../../../apis'
import { useState } from 'react'
import { Button, Card, Col, Form, Input, Row } from 'antd'

export const Test = () => {
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()
  const [result, setResult] = useState([])
  const [isMatched, setIsMatched] = useState(false)

  const handleTest = async values => {
    try {
      setLoading(true)
      const res = await getMatchTest(values)
      console.log(res)
      if (res?.data?.isMatched) {
        setResult(res?.data?.matches || [])
      } else {
        setResult(['未匹配到任何结果。'])
      }
      setIsMatched(res?.data?.isMatched)
    } catch (error) {
      setResult([error?.detail || JSON.stringify(error)])
      console.log(error)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="my-4">
      <Card title="测试识别">
        <Row gutter={24}>
          <Col md={12} sm={24}>
            <Form
              form={form}
              layout="horizontal"
              onFinish={handleTest}
              className="px-6 pb-6"
            >
              {/* token输入 */}
              <Form.Item
                name="apiToken"
                label="输入弹幕token"
                rules={[{ required: true, message: '请输入弹幕token' }]}
              >
                <Input placeholder="请输入弹幕token" />
              </Form.Item>

              {/* 文件名输入 */}
              <Form.Item
                name="fileName"
                label="文件名"
                rules={[
                  { required: true, message: '请输入要测试匹配的文件名' },
                ]}
              >
                <Input placeholder="请输入要测试匹配的文件名" />
              </Form.Item>

              {/* 测试按钮 */}
              <Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={loading}
                  className="w-full h-11 text-base font-medium rounded-lg bg-primary hover:bg-primary/90 transition-all duration-300 transform hover:scale-[1.02] active:scale-[0.98]"
                >
                  测试
                </Button>
              </Form.Item>
            </Form>
          </Col>
          <Col md={12} sm={24}>
            <div className="max-h-[144px] overflow-y-auto">
              {!!result?.length ? (
                <>
                  {isMatched ? (
                    <>
                      <div>[匹配成功]</div>
                      {result.map((it, index) => {
                        return (
                          <div key={index}>
                            番剧: {it.animeTitle} (ID: {it.animeId})
                            <br />
                            分集: {it.episodeTitle} (ID: {it.episodeId})
                            <br />
                            类型: {it.typeDescription}
                          </div>
                        )
                      })}
                    </>
                  ) : (
                    <>
                      <div>[匹配失败]</div>
                      {result.map((it, index) => {
                        return <div key={index}>{it}</div>
                      })}
                    </>
                  )}
                </>
              ) : (
                <div>测试结果将显示在这里。</div>
              )}
            </div>
          </Col>
        </Row>
      </Card>
    </div>
  )
}
