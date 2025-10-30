import React, { useState, useEffect } from 'react';
import { Modal, Form, Input, Select, Switch, Button, message, Checkbox, Space } from 'antd';
import { createMediaServer, updateMediaServer, testMediaServerConnection, getMediaServerLibraries } from '../../../apis';

const { Option } = Select;

const ServerConfigPanel = ({ visible, server, onClose, onSaved }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [libraries, setLibraries] = useState([]);
  const [loadingLibraries, setLoadingLibraries] = useState(false);

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
          selectedLibraries: server.selectedLibraries || [],
        });
        // 加载媒体库列表
        if (server.id) {
          loadLibraries(server.id);
        }
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

  const loadLibraries = async (serverId) => {
    setLoadingLibraries(true);
    try {
      const data = await getMediaServerLibraries(serverId);
      setLibraries(data);
    } catch (error) {
      console.error('加载媒体库列表失败:', error);
    } finally {
      setLoadingLibraries(false);
    }
  };

  const handleTest = async () => {
    try {
      await form.validateFields(['url', 'apiToken', 'providerName']);
      const values = form.getFieldsValue(['url', 'apiToken', 'providerName']);
      
      setTesting(true);
      
      // 如果是编辑模式,使用server.id测试
      if (server && server.id) {
        const result = await testMediaServerConnection(server.id);
        if (result.success) {
          message.success('连接成功!');
          // 重新加载媒体库列表
          await loadLibraries(server.id);
        } else {
          message.error('连接失败: ' + (result.message || '未知错误'));
        }
      } else {
        message.info('请先保存服务器配置后再测试连接');
      }
    } catch (error) {
      if (error.errorFields) {
        message.warning('请先填写必填字段');
      } else {
        message.error('测试失败: ' + (error.message || '未知错误'));
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
        message.success('服务器配置已更新');
      } else {
        // 创建
        await createMediaServer(values);
        message.success('服务器已添加');
      }

      onSaved();
    } catch (error) {
      if (error.errorFields) {
        message.warning('请填写所有必填字段');
      } else {
        message.error('保存失败: ' + (error.message || '未知错误'));
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title={server ? '编辑媒体服务器' : '添加媒体服务器'}
      open={visible}
      onCancel={onClose}
      width={600}
      footer={[
        <Button key="cancel" onClick={onClose}>
          取消
        </Button>,
        <Button key="test" onClick={handleTest} loading={testing} disabled={!server}>
          测试连接
        </Button>,
        <Button key="submit" type="primary" onClick={handleSubmit} loading={loading}>
          保存
        </Button>,
      ]}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          label="服务器名称"
          name="name"
          rules={[{ required: true, message: '请输入服务器名称' }]}
        >
          <Input placeholder="例如: 我的Emby服务器" />
        </Form.Item>

        <Form.Item
          label="服务器类型"
          name="providerName"
          rules={[{ required: true, message: '请选择服务器类型' }]}
        >
          <Select placeholder="请选择">
            <Option value="emby">Emby</Option>
            <Option value="jellyfin">Jellyfin</Option>
            <Option value="plex">Plex</Option>
          </Select>
        </Form.Item>

        <Form.Item
          label="服务器地址"
          name="url"
          rules={[
            { required: true, message: '请输入服务器地址' },
            { type: 'url', message: '请输入有效的URL' }
          ]}
        >
          <Input placeholder="http://localhost:8096" />
        </Form.Item>

        <Form.Item
          label="API Token"
          name="apiToken"
          rules={[{ required: true, message: '请输入API Token' }]}
        >
          <Input.Password placeholder="请输入API Token" />
        </Form.Item>

        <Form.Item
          label="启用状态"
          name="isEnabled"
          valuePropName="checked"
        >
          <Switch checkedChildren="启用" unCheckedChildren="禁用" />
        </Form.Item>

        {libraries.length > 0 && (
          <Form.Item
            label="选择媒体库"
            name="selectedLibraries"
            tooltip="留空则扫描所有媒体库"
          >
            <Checkbox.Group style={{ width: '100%' }}>
              <Space direction="vertical">
                {libraries.map(lib => (
                  <Checkbox key={lib.id} value={lib.id}>
                    {lib.name} ({lib.type})
                  </Checkbox>
                ))}
              </Space>
            </Checkbox.Group>
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
};

export default ServerConfigPanel;

