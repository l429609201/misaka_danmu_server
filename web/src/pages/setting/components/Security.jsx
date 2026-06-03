import { useState, useEffect, useCallback } from 'react'
import {
  Card, Switch, Button, Space, Typography, Divider, Input, Modal,
  List, Tag, message, Popconfirm, Spin, QRCode, Empty
} from 'antd'
import {
  SafetyOutlined, KeyOutlined, DeleteOutlined, EditOutlined,
  PlusOutlined, CheckCircleOutlined, CloseCircleOutlined, CopyOutlined
} from '@ant-design/icons'
import {
  getMfaStatus, setupTotp, verifyTotpSetup, disableTotp,
  getPasskeyRegisterOptions, verifyPasskeyRegister,
  renamePasskey, deletePasskey
} from '../../../apis'
import { base64urlToBuffer, bufferToBase64url } from '../../../components/MfaVerifyModal'
import { isPasskeySupported } from '../../../utils/passkey'
import { useTranslation } from 'react-i18next'

const { Text, Paragraph } = Typography

const Security = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [mfaStatus, setMfaStatus] = useState({ totpEnabled: false, passkeyCount: 0, passkeys: [] })

  // TOTP 状态
  const [totpSetupData, setTotpSetupData] = useState(null)
  const [totpCode, setTotpCode] = useState('')
  const [totpSetupLoading, setTotpSetupLoading] = useState(false)
  const [disablePassword, setDisablePassword] = useState('')
  const [disableModalOpen, setDisableModalOpen] = useState(false)

  // PassKey 状态
  const [registerLoading, setRegisterLoading] = useState(false)
  const [renameModalOpen, setRenameModalOpen] = useState(false)
  const [renameTarget, setRenameTarget] = useState(null)
  const [newDeviceName, setNewDeviceName] = useState('')

  const fetchStatus = useCallback(async () => {
    try {
      setLoading(true)
      const res = await getMfaStatus()
      setMfaStatus(res.data)
    } catch (err) {
      console.error('获取 MFA 状态失败:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchStatus() }, [fetchStatus])

  // ========== TOTP ==========
  const handleSetupTotp = async () => {
    try {
      setTotpSetupLoading(true)
      const res = await setupTotp()
      setTotpSetupData(res.data)
    } catch (err) {
      message.error(err.response?.data?.detail || t('security.setupTotpFailed'))
    } finally {
      setTotpSetupLoading(false)
    }
  }

  const handleVerifyTotp = async () => {
    if (!totpCode || totpCode.length !== 6) {
      message.warning(t('security.inputCode6'))
      return
    }
    try {
      await verifyTotpSetup({ code: totpCode })
      message.success(t('security.totpEnabledSuccess'))
      setTotpSetupData(null)
      setTotpCode('')
      fetchStatus()
    } catch (err) {
      message.error(err.response?.data?.detail || t('security.codeError'))
    }
  }

  const handleDisableTotp = async () => {
    try {
      await disableTotp({ password: disablePassword })
      message.success(t('security.totpDisabledSuccess'))
      setDisableModalOpen(false)
      setDisablePassword('')
      fetchStatus()
    } catch (err) {
      message.error(err.response?.data?.detail || t('security.disableFailed'))
    }
  }

  // ========== PassKey ==========
  const handleRegisterPasskey = async () => {
    if (!isPasskeySupported()) {
      message.error(t('security.passkeyHttpsOnlyError'))
      return
    }
    setRegisterLoading(true)
    try {
      const optRes = await getPasskeyRegisterOptions()
      const options = JSON.parse(optRes.data.options)
      options.challenge = base64urlToBuffer(options.challenge)
      options.user.id = base64urlToBuffer(options.user.id)
      if (options.excludeCredentials) {
        options.excludeCredentials = options.excludeCredentials.map(c => ({
          ...c, id: base64urlToBuffer(c.id)
        }))
      }

      const credential = await navigator.credentials.create({ publicKey: options })
      const credJSON = JSON.stringify({
        id: credential.id,
        rawId: credential.id,
        type: credential.type,
        response: {
          attestationObject: bufferToBase64url(credential.response.attestationObject),
          clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
        },
      })

      const deviceName = prompt(t('security.promptDeviceName'), t('security.promptDefaultName'))
      await verifyPasskeyRegister({ credential: credJSON, deviceName: deviceName || t('security.promptDefaultName') })
      message.success(t('security.passkeyRegisterSuccess'))
      fetchStatus()
    } catch (err) {
      if (err.name === 'NotAllowedError') {
        message.info(t('security.passkeyRegisterCancelled'))
      } else {
        message.error(t('security.passkeyRegisterFailed'))
      }
    } finally {
      setRegisterLoading(false)
    }
  }

  const handleRename = async () => {
    if (!renameTarget || !newDeviceName.trim()) return
    try {
      await renamePasskey(renameTarget.id, { deviceName: newDeviceName.trim() })
      message.success(t('security.renameSuccess'))
      setRenameModalOpen(false)
      fetchStatus()
    } catch (err) {
      message.error(t('security.renameFailed'))
    }
  }

  const handleDelete = async (id) => {
    try {
      await deletePasskey(id)
      message.success(t('security.deleteSuccess'))
      fetchStatus()
    } catch (err) {
      message.error(t('security.deleteFailed'))
    }
  }

  if (loading) return <Spin className="block mx-auto mt-12" />

  return (
    <div className="space-y-6">
      {/* TOTP 两步验证 */}
      <Card title={<><SafetyOutlined className="mr-2" />{t('security.totpTitle')}</>} size="small">
        <div className="flex items-center justify-between mb-4">
          <div>
            <Text>{t('security.totpDesc')}</Text>
            <br />
            <Text type="secondary">{t('security.totpSupportedApps')}</Text>
          </div>
          {mfaStatus.totpEnabled ? (
            <Space>
              <Tag color="green" icon={<CheckCircleOutlined />}>{t('security.totpEnabled')}</Tag>
              <Button danger size="small" onClick={() => setDisableModalOpen(true)}>{t('security.btnDisable')}</Button>
            </Space>
          ) : (
            <Button type="primary" size="small" onClick={handleSetupTotp} loading={totpSetupLoading}>
              {t('security.btnEnable')}
            </Button>
          )}
        </div>

        {/* TOTP 设置流程 */}
        {totpSetupData && (
          <div className="border rounded-lg p-4 mt-2">
            <Text strong>{t('security.totpStep1')}</Text>
            <div className="flex justify-center my-4">
              <QRCode value={totpSetupData.uri} size={200} />
            </div>
            <Text type="secondary">{t('security.totpManualKey')}</Text>
            <Paragraph copyable className="font-mono bg-gray-50 dark:bg-gray-800 p-2 rounded mt-1">
              {totpSetupData.secret}
            </Paragraph>
            <Divider />
            <Text strong>{t('security.totpStep2')}</Text>
            <div className="mt-2">
              <Space.Compact>
                <Input
                  placeholder={t('security.totpCodePlaceholder')}
                  maxLength={6}
                  value={totpCode}
                  onChange={e => setTotpCode(e.target.value.replace(/\D/g, ''))}
                  onPressEnter={handleVerifyTotp}
                  style={{ width: 160 }}
                />
                <Button type="primary" onClick={handleVerifyTotp}>{t('security.btnConfirmEnable')}</Button>
              </Space.Compact>
              <Button type="link" onClick={() => { setTotpSetupData(null); setTotpCode('') }}>{t('security.btnCancel')}</Button>
            </div>
          </div>
        )}
      </Card>

      {/* PassKey */}
      <Card
        title={<><KeyOutlined className="mr-2" />{t('security.passkeyTitle')}</>}
        size="small"
        extra={
          isPasskeySupported() && mfaStatus.totpEnabled ? (
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined />}
              onClick={handleRegisterPasskey}
              loading={registerLoading}
            >
              {t('security.btnRegisterPasskey')}
            </Button>
          ) : null
        }
      >
        {!isPasskeySupported() ? (
          <div className="text-center py-6">
            <Text type="secondary">
              <SafetyOutlined className="mr-1" />
              {t('security.passkeyHttpsOnly')}
            </Text>
            <br />
            <Text type="secondary" className="text-xs">
              {t('security.passkeyHttpsHint')}
            </Text>
          </div>
        ) : !mfaStatus.totpEnabled ? (
          <div className="text-center py-6">
            <Text type="secondary">
              <SafetyOutlined className="mr-1" />
              {t('security.passkeyRequireTotp')}
            </Text>
            <br />
            <Text type="secondary" className="text-xs">
              {t('security.passkeyRequireTotpHint')}
            </Text>
          </div>
        ) : (
          <>
            <Text type="secondary" className="block mb-4">
              {t('security.passkeyDesc')}
            </Text>

        {mfaStatus.passkeys.length === 0 ? (
          <Empty description={t('security.passkeyEmpty')} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <List
            dataSource={mfaStatus.passkeys}
            renderItem={item => (
              <List.Item
                actions={[
                  <Button
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => {
                      setRenameTarget(item)
                      setNewDeviceName(item.deviceName || '')
                      setRenameModalOpen(true)
                    }}
                  >
                    {t('security.btnRename')}
                  </Button>,
                  <Popconfirm title={t('security.confirmDeletePasskey')} onConfirm={() => handleDelete(item.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />}>{t('security.btnDelete')}</Button>
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  avatar={<KeyOutlined style={{ fontSize: 20, marginTop: 4 }} />}
                  title={item.deviceName || t('security.unnamedDevice')}
                  description={
                    <Space size="small" wrap>
                      <Text type="secondary">
                        {t('security.registeredAt', { date: item.createdAt ? new Date(item.createdAt).toLocaleDateString() : '-' })}
                      </Text>
                      {item.lastUsedAt && (
                        <Text type="secondary">
                          {t('security.lastUsedAt', { date: new Date(item.lastUsedAt).toLocaleDateString() })}
                        </Text>
                      )}
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
          </>
        )}
      </Card>

      {/* 关闭 TOTP 弹窗 */}
      <Modal
        title={t('security.disableTotpTitle')}
        open={disableModalOpen}
        onCancel={() => { setDisableModalOpen(false); setDisablePassword('') }}
        onOk={handleDisableTotp}
        okText={t('security.btnConfirmDisable')}
        okButtonProps={{ danger: true }}
      >
        <Text>{t('security.disableTotpDesc')}</Text>
        {mfaStatus.passkeyCount > 0 && (
          <div className="mt-2 px-3 py-2 rounded bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800">
            <Text type="warning" className="text-sm">
              {t('security.disableTotpPasskeyWarning', { count: mfaStatus.passkeyCount })}
            </Text>
          </div>
        )}
        <Input.Password
          className="mt-3"
          placeholder={t('security.currentPasswordPlaceholder')}
          value={disablePassword}
          onChange={e => setDisablePassword(e.target.value)}
        />
      </Modal>

      {/* 重命名 PassKey 弹窗 */}
      <Modal
        title={t('security.renameTotpTitle')}
        open={renameModalOpen}
        onCancel={() => setRenameModalOpen(false)}
        onOk={handleRename}
      >
        <Input
          placeholder={t('security.deviceNamePlaceholder')}
          value={newDeviceName}
          onChange={e => setNewDeviceName(e.target.value)}
        />
      </Modal>
    </div>
  )
}

export default Security