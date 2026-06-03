import React, { useState, useEffect } from 'react';
import { Modal, Form, Input, Select, Switch, Button, message } from 'antd';
import { EyeOutlined, EyeInvisibleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { createMediaServer, updateMediaServer, testMediaServerConnection, deleteMediaServer } from '../../../apis';

const { Option } = Select;

const ServerConfigPanel = ({ visible, server, onClose, onSaved }) => {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [showToken, setShowToken] = useState(false);

  useEffect(() => {
    if (visible) {
      if (server) {
        // 编辑模式
        form.setFieldsValue({
          name: server.name,
          providerName: server.providerName,
          url: server.url,
          apiToken: server.apiToken,
          isEnabled: server.isEnabled,
        });
      } else {
        // 新增模式
        form.resetFields();
        form.setFieldsValue({
          isEnabled: true,
          providerName: 'emby',
        });
      }
    }
  }, [visible, server, form]);

  const handleTest = async () => {
    try {
      const values = await form.validateFields(['name', 'url', 'apiToken', 'providerName']);

      setTesting(true);

      // 统一使用临时保存然后测试的方式
      try {
        let tempServer;
        if (server && server.id) {
          // 编辑模式: 先临时更新服务器配置
          tempServer = await updateMediaServer(server.id, { ...values, isEnabled: false });
        } else {
          // 新增模式: 先临时保存服务器
          tempServer = await createMediaServer({ ...values, isEnabled: false });
        }

        const res = await testMediaServerConnection(tempServer.data.id);
        const result = res.data;
        if (result.success) {
          message.success(t('mediaFetch.serverConfig.connectSuccess'));
        } else {
          message.error(t('mediaFetch.serverConfig.connectFailed') + (result.message || t('mediaFetch.serverConfig.unknownError')));
        }

        // 如果是新增模式，删除临时服务器
        if (!server || !server.id) {
          await deleteMediaServer(tempServer.data.id);
        }
      } catch (tempError) {
        message.error(t('mediaFetch.serverConfig.testFailed') + (tempError.message || t('mediaFetch.serverConfig.unknownError')));
      }
    } catch (error) {
      if (error.errorFields) {
        message.warning(t('mediaFetch.serverConfig.fillRequiredFirst'));
      } else {
        message.error(t('mediaFetch.serverConfig.testFailed') + (error.message || t('mediaFetch.serverConfig.unknownError')));
      }
    } finally {
      setTesting(false);
    }
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      setLoading(true);

      if (server) {
        // 更新
        await updateMediaServer(server.id, values);
        message.success(t('mediaFetch.serverConfig.serverUpdated'));
      } else {
        // 创建
        await createMediaServer(values);
        message.success(t('mediaFetch.serverConfig.serverAdded'));
      }

      onSaved();
    } catch (error) {
      if (error.errorFields) {
        message.warning(t('mediaFetch.serverConfig.fillAllRequired'));
      } else {
        message.error(t('mediaFetch.serverConfig.saveFailed') + (error.message || t('mediaFetch.serverConfig.unknownError')));
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title={server ? t('mediaFetch.serverConfig.editTitle') : t('mediaFetch.serverConfig.addTitle')}
      open={visible}
      onCancel={onClose}
      width={600}
      footer={[
        <Button key="cancel" onClick={onClose}>
          {t('mediaFetch.serverConfig.cancel')}
        </Button>,
        <Button key="test" onClick={handleTest} loading={testing}>
          {t('mediaFetch.serverConfig.testConnection')}
        </Button>,
        <Button key="submit" type="primary" onClick={handleSubmit} loading={loading}>
          {t('mediaFetch.serverConfig.save')}
        </Button>,
      ]}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          label={t('mediaFetch.serverConfig.serverName')}
          name="name"
          rules={[{ required: true, message: t('mediaFetch.serverConfig.serverNameRequired') }]}
        >
          <Input placeholder={t('mediaFetch.serverConfig.serverNamePlaceholder')} />
        </Form.Item>

        <Form.Item
          label={t('mediaFetch.serverConfig.serverType')}
          name="providerName"
          rules={[{ required: true, message: t('mediaFetch.serverConfig.serverTypeRequired') }]}
        >
          <Select placeholder={t('mediaFetch.serverConfig.selectPlaceholder')}>
            <Option value="emby">Emby</Option>
            <Option value="jellyfin">Jellyfin</Option>
            <Option value="plex">Plex</Option>
          </Select>
        </Form.Item>

        <Form.Item
          label={t('mediaFetch.serverConfig.serverAddress')}
          name="url"
          rules={[
            { required: true, message: t('mediaFetch.serverConfig.serverAddressRequired') },
            { type: 'url', message: t('mediaFetch.serverConfig.urlInvalid') }
          ]}
        >
          <Input placeholder="http://localhost:8096" />
        </Form.Item>

        <Form.Item
          label="API Token"
          name="apiToken"
          rules={[{ required: true, message: t('mediaFetch.serverConfig.apiTokenRequired') }]}
        >
          <Input
            placeholder={t('mediaFetch.serverConfig.apiTokenPlaceholder')}
            type={showToken ? 'text' : 'password'}
            suffix={
              showToken ? (
                <EyeOutlined onClick={() => setShowToken(false)} style={{ cursor: 'pointer' }} />
              ) : (
                <EyeInvisibleOutlined onClick={() => setShowToken(true)} style={{ cursor: 'pointer' }} />
              )
            }
          />
        </Form.Item>

        <Form.Item
          label={t('mediaFetch.serverConfig.enableStatus')}
          name="isEnabled"
          valuePropName="checked"
        >
          <Switch checkedChildren={t('mediaFetch.serverConfig.enable')} unCheckedChildren={t('mediaFetch.serverConfig.disable')} />
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default ServerConfigPanel;
