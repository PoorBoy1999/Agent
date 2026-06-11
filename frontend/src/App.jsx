import React, { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import './App.css'

// 待确认操作卡片组件 - 根据不同操作类型显示不同内容
const ConfirmationCard = ({ preview, onConfirm, onCancel }) => {
  // 如果 preview 不存在，尝试从不同路径获取数据
  const operation = preview?.operation || preview?.tool_name;
  
  // 文件写入操作卡片
  if (operation === 'write_file') {
    return (
      <div className="confirmation-card">
        <div className="confirmation-header">
          <span className="confirmation-icon">📄</span>
          <span className="confirmation-title">文件写入</span>
          <span className="operation-type">{preview.will_overwrite ? '覆盖已有文件' : '创建新文件'}</span>
        </div>
        <div className="confirmation-body">
          <div className="preview-info">
            <span className="preview-label">目标文件：</span>
            <code className="preview-value">{preview.file_path}</code>
          </div>
          <div className="preview-info">
            <span className="preview-label">内容统计：</span>
            <span className="preview-value">
              {preview.stats?.lines || 0} 行，{preview.stats?.characters || 0} 字符
            </span>
          </div>
          <div className="preview-content">
            <span className="preview-label">内容预览：</span>
            <pre className="content-preview">{preview.content_preview}</pre>
          </div>
        </div>
        <div className="confirmation-actions">
          <button className="confirm-btn" onClick={onConfirm}>确认执行</button>
          <button className="cancel-btn" onClick={onCancel}>取消</button>
        </div>
      </div>
    );
  }

  // 创建日历事件卡片
  if (operation === 'create_calendar_event') {
    return (
      <div className="confirmation-card calendar-card">
        <div className="confirmation-header">
          <span className="confirmation-icon">📅</span>
          <span className="confirmation-title">创建日程</span>
        </div>
        <div className="confirmation-body">
          <div className="preview-info">
            <span className="preview-label">日程标题：</span>
            <span className="preview-value highlight">{preview.subject}</span>
          </div>
          <div className="preview-info">
            <span className="preview-label">开始时间：</span>
            <span className="preview-value">{preview.start}</span>
          </div>
          <div className="preview-info">
            <span className="preview-label">结束时间：</span>
            <span className="preview-value">{preview.end}</span>
          </div>
          {preview.location && (
            <div className="preview-info">
              <span className="preview-label">会议地点：</span>
              <span className="preview-value">{preview.location}</span>
            </div>
          )}
          {preview.all_day && (
            <div className="preview-info">
              <span className="preview-label">全天事件：</span>
              <span className="preview-value">是</span>
            </div>
          )}
          {preview.body && (
            <div className="preview-info">
              <span className="preview-label">备注：</span>
              <span className="preview-value">{preview.body}</span>
            </div>
          )}
        </div>
        <div className="confirmation-actions">
          <button className="confirm-btn" onClick={onConfirm}>确认创建</button>
          <button className="cancel-btn" onClick={onCancel}>取消</button>
        </div>
      </div>
    );
  }

  // 更新日历事件卡片
  if (operation === 'update_calendar_event') {
    const changes = preview.changes || [];
    return (
      <div className="confirmation-card calendar-card update-card">
        <div className="confirmation-header">
          <span className="confirmation-icon">✏️</span>
          <span className="confirmation-title">更新日程</span>
        </div>
        <div className="confirmation-body">
          <div className="changes-list">
            <span className="changes-label">将要修改：</span>
            {changes.map((change, idx) => (
              <div key={idx} className="change-item">
                <span className="change-field">{change.field}</span>
                <span className="change-arrow">→</span>
                <span className="change-value">{change.new_value || '(空)'}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="confirmation-actions">
          <button className="confirm-btn" onClick={onConfirm}>确认更新</button>
          <button className="cancel-btn" onClick={onCancel}>取消</button>
        </div>
      </div>
    );
  }

  // 写邮件草稿卡片
  if (operation === 'write_email_draft') {
    return (
      <div className="confirmation-card email-card">
        <div className="confirmation-header">
          <span className="confirmation-icon">✉️</span>
          <span className="confirmation-title">邮件草稿</span>
        </div>
        <div className="confirmation-body">
          <div className="preview-info">
            <span className="preview-label">收件人：</span>
            <span className="preview-value">{preview.to}</span>
          </div>
          <div className="preview-info">
            <span className="preview-label">邮件主题：</span>
            <span className="preview-value highlight">{preview.subject}</span>
          </div>
          <div className="preview-info">
            <span className="preview-label">统计：</span>
            <span className="preview-value">{preview.body_lines} 行，{preview.body_characters} 字符</span>
          </div>
          {preview.cc && preview.cc !== '（无）' && (
            <div className="preview-info">
              <span className="preview-label">抄送：</span>
              <span className="preview-value">{preview.cc}</span>
            </div>
          )}
          <div className="preview-content">
            <span className="preview-label">正文预览：</span>
            <pre className="content-preview">{preview.body_preview}</pre>
          </div>
          <div className="preview-info">
            <span className="preview-label">保存位置：</span>
            <span className="preview-value">{preview.save_location}</span>
          </div>
        </div>
        <div className="confirmation-actions">
          <button className="confirm-btn" onClick={onConfirm}>确认创建</button>
          <button className="cancel-btn" onClick={onCancel}>取消</button>
        </div>
      </div>
    );
  }

  // 创建待办事项卡片
  if (operation === 'create_todo') {
    return (
      <div className="confirmation-card todo-card">
        <div className="confirmation-header">
          <span className="confirmation-icon">📝</span>
          <span className="confirmation-title">待办事项</span>
        </div>
        <div className="confirmation-body">
          <div className="preview-info">
            <span className="preview-label">待办内容：</span>
            <span className="preview-value highlight">{preview.content}</span>
          </div>
          {preview.due_date && (
            <div className="preview-info">
              <span className="preview-label">截止日期：</span>
              <span className="preview-value">{preview.due_date}</span>
            </div>
          )}
          {preview.due_time && (
            <div className="preview-info">
              <span className="preview-label">截止时间：</span>
              <span className="preview-value">{preview.due_time}</span>
            </div>
          )}
          <div className="preview-info">
            <span className="preview-label">优先级：</span>
            <span className={`preview-value priority-${preview.priority}`}>
              {preview.priority === 'high' ? '高' : preview.priority === 'low' ? '低' : '普通'}
            </span>
          </div>
        </div>
        <div className="confirmation-actions">
          <button className="confirm-btn" onClick={onConfirm}>确认创建</button>
          <button className="cancel-btn" onClick={onCancel}>取消</button>
        </div>
      </div>
    );
  }

  // 默认卡片（未知操作类型）
  return (
    <div className="confirmation-card">
      <div className="confirmation-header">
        <span className="confirmation-icon">⚠️</span>
        <span className="confirmation-title">待确认操作</span>
      </div>
      <div className="confirmation-body">
        <pre className="content-preview">{JSON.stringify(preview, null, 2)}</pre>
      </div>
      <div className="confirmation-actions">
        <button className="confirm-btn" onClick={onConfirm}>确认执行</button>
        <button className="cancel-btn" onClick={onCancel}>取消</button>
      </div>
    </div>
  );
};

// 工具调用日志类型定义
function App() {
  const [messages, setMessages] = useState([
    {
      id: 1,
      role: 'assistant',
      content: '你好！我是本地超级助手。我可以帮助你读取本地文件。\n\n你可以对我说："帮我读一下 \\"AI学习笔记\\" "来读取文件。',
      timestamp: new Date()
    }
  ])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [agentStatus, setAgentStatus] = useState('') // Agent 当前状态
  const [connectionStatus, setConnectionStatus] = useState('connecting') // connecting | connected | disconnected
  const [activeToolCall, setActiveToolCall] = useState(null)
  // 待确认操作状态
  const [pendingConfirmation, setPendingConfirmation] = useState(null) // { content, pending_data }
  // 工具调用列表 - 只存储工具名称
  const [toolCalls, setToolCalls] = useState([])
  // 执行步骤列表 - 用于展示当前正在执行的步骤
  const [executionSteps, setExecutionSteps] = useState([])
  
  // 调试：监听 toolCalls 变化
  useEffect(() => {
    console.log('[useEffect] toolCalls 更新了，长度:', toolCalls.length)
    if (toolCalls.length > 0) {
      console.log('[useEffect] toolCalls 内容:', JSON.stringify(toolCalls))
    }
  }, [toolCalls])
  
  const messagesEndRef = useRef(null)
  const textareaRef = useRef(null)
  const wsRef = useRef(null)
  const isConnectedRef = useRef(false)
  const toolCallsRef = useRef([]) // 用于在闭包中访问最新的 toolCalls

  // 自动调整输入框高度
  const autoResizeTextarea = () => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = 'auto'
      const newHeight = Math.min(Math.max(textarea.scrollHeight, 44), 200)
      textarea.style.height = `${newHeight}px`
    }
  }

  // 滚动到底部
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  // WebSocket 连接
  useEffect(() => {
    // 避免重复连接
    if (isConnectedRef.current) return

    const ws = new WebSocket('ws://localhost:8000/ws/chat')
    wsRef.current = ws
    setConnectionStatus('connecting')

    ws.onopen = () => {
      console.log('WebSocket 连接成功')
      isConnectedRef.current = true
      setConnectionStatus('connected')
    }

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      handleWebSocketMessage(data)
    }

    ws.onerror = (error) => {
      console.error('WebSocket 错误:', error)
      setConnectionStatus('disconnected')
    }

    ws.onclose = () => {
      console.log('WebSocket 连接关闭')
      isConnectedRef.current = false
      setConnectionStatus('disconnected')
    }

    return () => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.close()
      }
      isConnectedRef.current = false
    }
  }, [])

  // 处理 WebSocket 消息
  const handleWebSocketMessage = (data) => {
    console.log('[WebSocket] 收到消息:', data.type)
    switch (data.type) {
      case 'status':
        setIsLoading(true)
        setAgentStatus(data.content) // 保存状态信息（agentStatus 会在侧边栏和聊天区同时显示）
        // 注意：不要添加执行步骤，因为 agentStatus 已经会在侧边栏显示
        break

      case 'tool_call_start':
        console.log('[WebSocket] tool_call_start:', data.tool_name)
        setActiveToolCall({
          name: data.tool_name,
          params: data.params,
          status: 'running'
        })
        // 添加到工具调用列表
        addToolCall(data.tool_name)
        break

      case 'tool_call_result': {
        console.log('[WebSocket] tool_call_result:', data.tool_name)
        const r = data.result ?? {}
        const ok = r.success === true
        const detail = ok
          ? (r.file_path ?? r.content?.slice?.(0, 80) ?? '')
          : (r.error ?? '未知错误')
        setActiveToolCall(prev => ({
          ...prev,
          result: r,
          status: ok ? 'success' : 'error'
        }))
        // 更新工具调用列表中对应工具的状态
        updateToolCallStatus(data.tool_name, ok ? 'success' : 'error', detail)
        break
      }

      case 'plan_info':
        // 收到执行计划信息
        console.log('[WebSocket] 收到 plan_info:', data.content)
        setAgentStatus(data.content || '正在分析计划...')
        break

      case 'refresh_todos':
        // 待办列表更新通知
        console.log('[WebSocket] 收到待办更新通知:', data)
        addExecutionStep(`待办已${data.action === 'created' ? '创建' : data.action === 'completed' ? '完成' : '更新'}`)
        // 可以触发获取最新待办列表
        if (data.action === 'created' && data.todo) {
          addExecutionStep(`新建待办: ${data.todo.content}`)
        }
        break

      case 'response':
        console.log('[WebSocket] 收到 response，准备关闭 loading')
        console.log('[WebSocket] 完整 data:', JSON.stringify(data, null, 2))
        console.log('[WebSocket] tool_calls:', data.tool_calls)
        console.log('[WebSocket] messages:', data.messages ? `${data.messages.length} 条` : 'null')
        console.log('[WebSocket] current toolCalls length:', toolCallsRef.current.length)
        setIsLoading(false)
        setAgentStatus('') // 清除状态
        setActiveToolCall(null)
        
        // 如果工具调用列表为空，尝试从 response 中提取工具列表
        if (toolCallsRef.current.length === 0) {
          console.log('[WebSocket] 工具列表为空，开始提取...')
          
          // 优先从 response.tool_calls 提取
          if (data.tool_calls && data.tool_calls.length > 0) {
            console.log('[WebSocket] 从 response.tool_calls 填充工具列表')
            data.tool_calls.forEach(tool => {
              const toolName = tool.name || tool.tool_name || 'tool'
              console.log('[WebSocket] 添加工具:', toolName)
              addToolCall(toolName)
              updateToolCallStatus(toolName, 'success', '')
            })
          }
          // 备用：从 messages 中提取 role 为 'tool' 的消息
          else if (data.messages && data.messages.length > 0) {
            console.log('[WebSocket] 尝试从 messages 中提取工具')
            const toolMessages = data.messages.filter(m => m.role === 'tool' && m.tool_name)
            console.log('[WebSocket] toolMessages 数量:', toolMessages.length)
            if (toolMessages.length > 0) {
              console.log('[WebSocket] 从 messages 中提取工具列表')
              toolMessages.forEach(m => {
                console.log('[WebSocket] 添加工具:', m.tool_name)
                addToolCall(m.tool_name)
                updateToolCallStatus(m.tool_name, 'success', m.content?.slice?.(0, 80) || '')
              })
            }
          } else {
            console.log('[WebSocket] 没有找到任何工具调用信息')
          }
        } else {
          console.log('[WebSocket] 工具列表已有内容，跳过提取')
        }
        
        // 前端已有完整的消息历史，不需要从后端获取
        // 只添加一条新的响应消息，并从 tool_calls 中提取工具调用信息
        console.log('[WebSocket] 添加响应消息到前端已有消息')
        setMessages(prev => {
          // 过滤掉临时的 assistant 消息（空的或未完成的）
          const filteredPrev = prev.filter(m => 
            m.role !== 'assistant' || 
            (m.content && m.content.trim() && !m.isClarification && !m.isConfirmationResult)
          )
          
          // 添加新的响应消息
          return [...filteredPrev, {
            id: Date.now(),
            role: 'assistant',
            originalRole: 'assistant',
            content: data.content,
            timestamp: new Date(),
            toolCalls: data.tool_calls || []
          }]
        })
        // break 应该在这里，而不是在回调内部
        break

      case 'pending_confirmation':
        // 需要用户确认的操作
        console.log('[WebSocket] 收到待确认消息')
        setIsLoading(false)
        setPendingConfirmation({
          content: data.content,
          pending_data: data.pending_data
        })
        addExecutionStep('等待用户确认')
        // 添加一条提示消息到聊天列表，告知用户需要确认操作
        setMessages(prev => [...prev, {
          id: Date.now(),
          role: 'assistant',
          content: data.content,
          timestamp: new Date(),
          isConfirmation: true,
          pending_data: data.pending_data,
          showCard: true // 标记显示卡片
        }])
        break

      case 'confirmation_result':
        // 确认操作的结果
        console.log('[WebSocket] 收到确认结果:', data.confirmed)
        setIsLoading(false)
        setAgentStatus('') // 清除状态
        setPendingConfirmation(null)
        
        if (data.confirmed) {
          addExecutionStep('操作执行成功')
        } else {
          addExecutionStep('操作已取消')
        }
        
        // 显示确认结果消息
        setMessages(prev => [...prev, {
          id: Date.now(),
          role: 'assistant',
          content: data.content,
          timestamp: new Date(),
          isConfirmationResult: true,
          confirmed: data.confirmed
        }])
        break

      case 'clarification':
        // 需要用户补充参数信息
        console.log('[WebSocket] 收到追问:', data.content)
        setIsLoading(false)
        setAgentStatus('') // 清除状态
        addExecutionStep(`需要补充信息: ${data.missing_params?.join(', ')}`)
        
        // 显示参数进度 (例如: start 和 end → 显示 1/2, 2/2)
        const paramsCount = data.missing_params_count || data.missing_params?.length || 1
        const currentIndex = data.current_param_index || 1
        
        setMessages(prev => [...prev, {
          id: Date.now(),
          role: 'assistant',
          content: data.content,
          timestamp: new Date(),
          isClarification: true,
          questionNumber: data.question_number,
          maxQuestions: data.max_questions,
          missingParams: data.missing_params,
          paramsProgress: `${currentIndex}/${paramsCount}`,
          paramsCount: paramsCount
        }])
        break

      case 'error':
        console.log('[WebSocket] 收到错误:', data.content)
        setIsLoading(false)
        setAgentStatus('') // 清除状态
        setActiveToolCall(null)
        addExecutionStep(`错误: ${data.content}`)
        break

      default:
        console.warn('[WebSocket] 未知消息类型:', data.type)
    }
  }

  // 添加工具调用
  const addToolCall = (toolName) => {
    console.log('[addToolCall] 开始添加工具:', toolName)
    setToolCalls(prev => {
      const newList = [...prev, {
        id: Date.now() + Math.random(),
        name: toolName,
        status: 'running',
        timestamp: new Date()
      }]
      console.log('[addToolCall] prev.length:', prev.length, '-> newList.length:', newList.length)
      toolCallsRef.current = newList
      return newList
    })
    console.log('[addToolCall] setToolCalls 已调用')
  }

  // 更新工具调用状态
  const updateToolCallStatus = (toolName, status, detail = '') => {
    console.log('[updateToolCallStatus] 更新工具:', toolName, '状态:', status)
    setToolCalls(prev => {
      console.log('[updateToolCallStatus] 当前列表长度:', prev.length)
      const newList = prev.map(tool => 
        tool.name === toolName 
          ? { ...tool, status, detail, endTime: new Date() }
          : tool
      )
      toolCallsRef.current = newList
      console.log('[updateToolCallStatus] 更新后列表长度:', newList.length)
      return newList
    })
  }

  // 添加执行步骤
  const addExecutionStep = (step) => {
    setExecutionSteps(prev => [...prev, {
      id: Date.now() + Math.random(),
      content: step,
      timestamp: new Date()
    }])
  }

  // 清除本次对话的临时状态
  const clearSessionState = () => {
    setToolCalls([])
    setExecutionSteps([])
  }

  // 发送消息
  const sendMessage = async () => {
    if (!input.trim() || isLoading) return

    const userMessage = {
      id: Date.now(),
      role: 'user',
      originalRole: 'user',
      content: input,
      timestamp: new Date()
    }

    setMessages(prev => [...prev, userMessage])
    setInput('')
    setIsLoading(true)
    // 不清除 toolCalls，让它们保留显示，直到收到新的 response 消息
    // clearSessionState() // 已移除，避免清除刚添加的工具调用

    // 发送后重置输入框高度
    setTimeout(() => {
      const textarea = textareaRef.current
      if (textarea) {
        textarea.style.height = 'auto'
      }
    }, 0)

    // 设置超时，防止一直卡在 loading 状态
    const timeoutId = setTimeout(() => {
      setIsLoading(false)
    }, 60000) // 60秒超时

    // 通过 WebSocket 发送
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        message: input,
        // 包含完整的 messages 历史，包括 tool 角色的消息
        history: messages.map(m => ({
          role: m.originalRole || m.role, // 优先使用原始角色，保留 tool 标记
          content: m.content,
          tool_name: m.toolCalls && m.toolCalls.length > 0 ? m.toolCalls[0].name : null
        })),
        timeoutId // 用于取消超时
      }))
    } else {
      clearTimeout(timeoutId)
      setIsLoading(false)
      addExecutionStep('WebSocket 未连接，请检查后端服务')
    }
  }

  // 确认执行待操作
  const confirmOperation = () => {
    if (!pendingConfirmation || !wsRef.current) return

    addExecutionStep('用户确认执行操作')
    
    wsRef.current.send(JSON.stringify({
      type: 'confirm',
      confirmed: true,
      pending_data: pendingConfirmation.pending_data
    }))
    
    setPendingConfirmation(null)
    setIsLoading(true)
  }

  // 取消待操作
  const cancelOperation = () => {
    if (!pendingConfirmation || !wsRef.current) return

    addExecutionStep('用户取消操作')
    
    wsRef.current.send(JSON.stringify({
      type: 'confirm',
      confirmed: false,
      pending_data: pendingConfirmation.pending_data
    }))
    
    setPendingConfirmation(null)
  }

  // 格式化时间
  const formatTime = (date) => {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  // 获取工具调用状态样式
  const getToolStatusIcon = (status) => {
    switch (status) {
      case 'success':
        return '✓'
      case 'error':
        return '✗'
      case 'running':
        return '⟳'
      default:
        return '○'
    }
  }

  // 获取工具调用状态样式
  const getToolStatusClass = (status) => {
    switch (status) {
      case 'success':
        return 'tool-success'
      case 'error':
        return 'tool-error'
      case 'running':
        return 'tool-running'
      default:
        return ''
    }
  }

  return (
    <div className="app">
      {/* 侧边栏 - 工具调用日志 */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <h2>工具调用日志</h2>
        </div>
        
        {/* 工具调用列表 */}
        <div className="tool-calls-section">
          <div className="tool-calls-header">
            <span>调用的工具</span>
            {toolCalls.length > 0 && (
              <span className="tool-count">{toolCalls.length}</span>
            )}
          </div>
          <div className="tool-calls-list">
            {toolCalls.length === 0 ? (
              <div className="tool-empty">暂无工具调用</div>
            ) : (
              toolCalls.map(tool => (
                <div key={tool.id} className={`tool-item ${getToolStatusClass(tool.status)}`}>
                  <span className="tool-icon">{getToolStatusIcon(tool.status)}</span>
                  <span className="tool-name">{tool.name}</span>
                  {tool.detail && (
                    <span className="tool-detail">{tool.detail}</span>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
        
        {/* 当前工具调用状态（详细） */}
        {activeToolCall && (
          <div className="active-tool">
            <div className="tool-status">
              <span className="tool-name">{activeToolCall.name}</span>
              <span className={`status-badge ${activeToolCall.status}`}>
                {activeToolCall.status === 'running' ? '运行中...' : '完成'}
              </span>
            </div>
            <div className="tool-params">
              参数: {JSON.stringify(activeToolCall.params)}
            </div>
          </div>
        )}
      </aside>

      {/* 主聊天区域 */}
      <main className="chat-area">
        <header className="chat-header">
          <h1>本地超级助手</h1>
          <div className="connection-status">
            <span className={`status-dot ${connectionStatus === 'connected' ? 'connected' : 'disconnected'}`}></span>
            {connectionStatus === 'connected' ? '已连接' : connectionStatus === 'connecting' ? '连接中...' : '未连接'}
          </div>
        </header>

        <div className="messages-container">
          {messages
            .filter(msg => {
              // 过滤掉工具消息（不显示）
              if (msg.role === 'tool') return false;
              // 过滤掉空的 assistant 消息
              if (msg.role === 'assistant' && !msg.content?.trim()) return false;
              // 过滤掉包含工具结果的 assistant 消息（JSON 格式）
              if (msg.role === 'assistant' && 
                  (msg.content?.startsWith('{') || 
                   msg.content?.includes("'success':") ||
                   msg.content?.includes('"success":'))) {
                return false;
              }
              return true;
            })
            .map(msg => (
            <div key={msg.id} className={`message message-${msg.role}`}>
              <div className="message-avatar">
                {msg.role === 'user' ? '👤' : '🤖'}
              </div>
              <div className="message-content">
                <div className="message-meta">
                  <span className="message-sender">
                    {msg.role === 'user' ? '你' : '助手'}
                  </span>
                  <span className="message-time">{formatTime(msg.timestamp)}</span>
                </div>
                <div className="message-text">
                  <ReactMarkdown className="markdown-content">
                    {msg.content}
                  </ReactMarkdown>
                </div>
                {msg.toolCalls && msg.toolCalls.length > 0 && (
                  <div className="message-tools">
                    <span className="tools-label">调用的工具:</span>
                    {msg.toolCalls.map((tool, idx) => (
                      <span key={idx} className="tool-tag">
                        {tool.name}
                      </span>
                    ))}
                  </div>
                )}
                {/* 待确认操作卡片 - 根据不同操作类型显示不同内容 */}
                {msg.isConfirmation && msg.pending_data && (
                  <ConfirmationCard
                    preview={msg.pending_data.preview || msg.pending_data}
                    onConfirm={confirmOperation}
                    onCancel={cancelOperation}
                  />
                )}

                {/* 追问消息卡片 */}
                {msg.isClarification && (
                  <div className="clarification-card">
                    <div className="clarification-header">
                      <span className="clarification-icon">💬</span>
                      <span className="clarification-title">需要补充信息</span>
                      <span className="clarification-progress">
                        ({msg.paramsProgress})
                      </span>
                    </div>
                    <div className="clarification-body">
                      <div className="missing-params">
                        <span className="params-label">缺少参数：</span>
                        <div className="param-tags">
                          {msg.missingParams?.map((param, idx) => (
                            <span key={idx} className="param-tag">{param}</span>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
          {isLoading && (
            <div className="message message-assistant">
              <div className="message-avatar">🤖</div>
              <div className="message-content">
                <div className="message-meta">
                  <span className="message-sender">助手</span>
                </div>
                <div className="message-loading">
                  {/* 只显示当前 agentStatus，不显示 executionSteps（避免与侧边栏重复） */}
                  {agentStatus ? (
                    <div className="loading-steps">
                      <div className="loading-step active">
                        <span className="loading-spinner"></span>
                        <span>{agentStatus}</span>
                      </div>
                    </div>
                  ) : (
                    <span className="typing-indicator">
                      <span></span><span></span><span></span>
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="input-area">
          <textarea
            className="message-input"
            ref={textareaRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value)
              autoResizeTextarea()
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
              }
            }}
            placeholder='输入消息... (例如: 帮我读一下 "AI学习笔记")'
            disabled={isLoading}
            rows={1}
          />
          <button 
            className="send-btn"
            onClick={sendMessage}
            disabled={isLoading || !input.trim()}
          >
            发送
          </button>
        </div>
      </main>
    </div>
  )
}

export default App
