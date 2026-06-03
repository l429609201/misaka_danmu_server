import { useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Input, Button, Space, Typography, Divider, message } from 'antd'
import { SafetyOutlined, KeyOutlined } from '@ant-design/icons'
import { mfaVerify, getPasskeyAuthOptions } from '../apis'
import { isPasskeySupported } from '../utils/passkey'

const { Text } = Typography

/**
 * MFA 验证弹窗组件
 * 支持 TOTP 验证码输入和 PassKey 认证
 *
 * Props:
 *  - mfaToken: 密码验证后获得的临时令牌
 *  - onSuccess(tokenData): MFA 验证成功，返回 JWT 数据
 */
export const MfaVerifyModal = ({ open, onCancel, onSuccess, mfaTypes = [], mfaToken = '', username = '' }) => {
  const { t } = useTranslation()
  const [otpCode, setOtpCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [passkeyLoading, setPasskeyLoading] = useState(false)

  const hasTotp = mfaTypes.includes('totp')
  // PassKey 仅在 HTTPS 模式下启用，避免和同 IP 不同端口的其它应用冲突
  const hasPasskey = mfaTypes.includes('passkey') && isPasskeySupported()

  // TOTP 验证 → 直接调 /mfa/verify 拿 JWT
  const handleTotpVerify = useCallback(async () => {
    if (!otpCode || otpCode.length !== 6) {
      message.warning(t('mfa.inputCode'))
      return
    }
    setLoading(true)
    try {
      const res = await mfaVerify({ mfa_token: mfaToken, otp_code: otpCode })
      onSuccess(res.data)
    } catch (err) {
      message.error(err.detail || err.message || t('mfa.codeError'))
    } finally {
      setLoading(false)
    }
  }, [otpCode, mfaToken, onSuccess, t])

  // PassKey 验证 → WebAuthn + /mfa/verify 拿 JWT
  const handlePasskeyVerify = useCallback(async () => {
    if (!isPasskeySupported()) {
      message.error(t('mfa.passkeyHttpsOnly'))
      return
    }

    setPasskeyLoading(true)
    try {
      // 1. 获取认证选项
      const optionsRes = await getPasskeyAuthOptions(username)
      const options = JSON.parse(optionsRes.data.options)

      // 2. 转换 base64url 为 ArrayBuffer
      options.challenge = base64urlToBuffer(options.challenge)
      if (options.allowCredentials) {
        options.allowCredentials = options.allowCredentials.map(cred => ({
          ...cred,
          id: base64urlToBuffer(cred.id),
        }))
      }

      // 3. 调用浏览器 WebAuthn API
      const credential = await navigator.credentials.get({ publicKey: options })

      // 4. 序列化凭证
      const credentialJSON = JSON.stringify({
        id: credential.id,
        rawId: credential.id,
        type: credential.type,
        response: {
          authenticatorData: bufferToBase64url(credential.response.authenticatorData),
          clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
          signature: bufferToBase64url(credential.response.signature),
          userHandle: credential.response.userHandle
            ? bufferToBase64url(credential.response.userHandle)
            : null,
        },
      })

      // 5. 调 /mfa/verify 拿 JWT（服务端验证 PassKey）
      const res = await mfaVerify({ mfa_token: mfaToken, passkey_credential: credentialJSON })
      onSuccess(res.data)
    } catch (err) {
      if (err.name === 'NotAllowedError') {
        message.info(t('mfa.passkeyCancelled'))
      } else {
        console.error('PassKey 验证失败:', err)
        message.error(t('mfa.passkeyFailed') + ': ' + (err.detail || err.message || t('mfa.unknownError')))
      }
    } finally {
      setPasskeyLoading(false)
    }
  }, [username, mfaToken, onSuccess, t])

  const handleKeyDown = e => {
    if (e.key === 'Enter' && hasTotp) {
      handleTotpVerify()
    }
  }

  return (
    <Modal
      title={t('mfa.title')}
      open={open}
      onCancel={onCancel}
      footer={null}
      destroyOnClose
      width="min(420px, calc(100vw - 32px))"
    >
      <div className="py-4">
        <Text type="secondary">
          {t('mfa.desc')}
        </Text>

        {hasTotp && (
          <div className="mt-4">
            <Text strong><SafetyOutlined className="mr-1" />{t('mfa.codeLabel')}</Text>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row">
              <Input
                id="one-time-code"
                name="one-time-code"
                placeholder={t('mfa.inputCode')}
                maxLength={6}
                value={otpCode}
                onChange={e => setOtpCode(e.target.value.replace(/\D/g, ''))}
                onKeyDown={handleKeyDown}
                size="large"
                autoFocus
                autoComplete="one-time-code"
                inputMode="numeric"
                pattern="[0-9]*"
                className="w-full"
              />
              <Button
                type="primary"
                size="large"
                loading={loading}
                onClick={handleTotpVerify}
                className="w-full sm:w-auto sm:min-w-20"
              >
                {t('mfa.verify')}
              </Button>
            </div>
          </div>
        )}

        {hasTotp && hasPasskey && (
          <Divider plain>{t('mfa.or')}</Divider>
        )}

        {hasPasskey && (
          <div className={hasTotp ? '' : 'mt-4'}>
            <Button
              block
              size="large"
              icon={<KeyOutlined />}
              loading={passkeyLoading}
              onClick={handlePasskeyVerify}
            >
              {t('mfa.usePasskey')}
            </Button>
          </div>
        )}
      </div>
    </Modal>
  )
}

// ========== WebAuthn 工具函数 ==========

function base64urlToBuffer(base64url) {
  const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/')
  const pad = base64.length % 4 === 0 ? '' : '='.repeat(4 - (base64.length % 4))
  const binary = atob(base64 + pad)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}

function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

// 导出工具函数供其他组件使用
export { base64urlToBuffer, bufferToBase64url }
