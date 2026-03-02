const { createApp, ref, onMounted, nextTick, watch, computed } = Vue;

createApp({
    setup() {
        const API_BASE = 'http://127.0.0.1:8000';
        const channelId = 'web_client_' + Math.floor(Math.random() * 10000);
        
        // --- 状态 ---
        const currentTab = ref('chat');
        const wsConnected = ref(false);
        const inputText = ref('');
        const messages = ref([]);
        let ws = null;
        const messageOffset = ref(0);
        const hasMoreMessages = ref(false);
        const isLoadingHistory = ref(false);
        
        // 任务树
        const historyTasks = ref([]);
        const activeSolveId = ref(null);
        const activeTaskName = computed(() => {
            if (!activeSolveId.value) return null;
            const task = historyTasks.value.find(t => t.solve_id === activeSolveId.value);
            return task ? task.goal : activeSolveId.value; // 查不到就降级显示 ID
        });
        let myChart = null;

        // 配置数据
        const llmConfig = ref({ defaults: {}, providers: {} });

        // Provider 表单状态
        const showProvModal = ref(false);
        const isEditProv = ref(false);
        const provForm = ref({ name: '', config: { base_url: '', api_key: '', api_type: 'openai-completions', models: [] } });

        // Model 表单状态
        const showModelModal = ref(false);
        const isEditModel = ref(false);
        const currentProvForModel = ref('');
        const defaultModelForm = () => ({
            id: '', description: '', enabled: true, capability_score: 1.0, reasoning: false,
            features: ['text', 'tool_use'], context_window: 100000, max_tokens: 4096,
            cost: { input_1m: 0, output_1m: 0 }
        });
        const modelForm = ref(defaultModelForm());

        // ==========================
        // 1. WebSocket
        // ==========================
        const getArtifactUrl = (uri) => {
            if (!uri) return '#';
            return `${API_BASE}/api/artifacts?uri=${encodeURIComponent(uri)}`;
        };

        const loadHistory = async () => {
            if (isLoadingHistory.value) return;
            isLoadingHistory.value = true;
            try {
                const box = document.getElementById('chat-box');
                const oldScrollHeight = box ? box.scrollHeight : 0;

                const res = await axios.get(`${API_BASE}/api/chat/history/${channelId}?offset=${messageOffset.value}&limit=10`);
                const { messages: historyMsgs, has_more } = res.data;
                
                messages.value = [...historyMsgs, ...messages.value];
                messageOffset.value += historyMsgs.length;
                hasMoreMessages.value = has_more;

                nextTick(() => {
                    if (box) {
                        if (messageOffset.value === historyMsgs.length) {
                            box.scrollTop = box.scrollHeight; // 首次加载滚到底部
                        } else {
                            box.scrollTop = box.scrollHeight - oldScrollHeight; // 维持阅读位置
                        }
                    }
                });
            } catch (e) { 
                console.error("获取历史记录失败", e); 
            } finally {
                isLoadingHistory.value = false;
            }
        };

        const initWebSocket = () => {
            ws = new WebSocket(`ws://127.0.0.1:8000/ws/chat/${channelId}`);
            ws.onopen = () => { wsConnected.value = true; };
            ws.onclose = () => { wsConnected.value = false; setTimeout(initWebSocket, 3000); };
            ws.onmessage = (event) => {
                const msg = JSON.parse(event.data);
                if (['message', 'report', 'heartbeat'].includes(msg.message_type)) {
                    messages.value.push(msg);
                    messageOffset.value += 1;
                    scrollToBottom();
                }
                
                // 只静默刷新列表与当前激活的树
                fetchHistoryTasks().then(() => {
                    if (activeSolveId.value) {
                        fetchTaskTree(activeSolveId.value);
                    }
                });
            };
        };

        const sendMessage = () => {
            if (!inputText.value.trim() || !wsConnected.value) return;
            messages.value.push({ sender: 'user', content: inputText.value });
            ws.send(inputText.value);
            messageOffset.value += 1;
            inputText.value = '';
            scrollToBottom();
        };

        const scrollToBottom = () => {
            nextTick(() => {
                const box = document.getElementById('chat-box');
                if(box) box.scrollTop = box.scrollHeight;
            });
        };

        // ==========================
        // 2. ECharts 任务树
        // ==========================
        const initECharts = () => {
            const chartDom = document.getElementById('echarts-container');
            if(chartDom) {
                myChart = echarts.init(chartDom);
                window.addEventListener('resize', () => myChart.resize());
            }
        };

        const fetchHistoryTasks = async () => {
            try {
                // 追加 _t 强制绕过浏览器 GET 缓存
                const res = await axios.get(`${API_BASE}/api/tasks?_t=${Date.now()}`);
                historyTasks.value = res.data.tasks;
                
                // 如果还没选中任务树，且历史记录不为空，默认展示最新的一个
                if (!activeSolveId.value && historyTasks.value.length > 0) {
                    fetchTaskTree(historyTasks.value[0].solve_id);
                }
            } catch (e) { console.error("获取历史任务失败", e); }
        };

        const fetchTaskTree = async (solveId) => {
            activeSolveId.value = solveId;
            try {
                const res = await axios.get(`${API_BASE}/api/tasks/${solveId}`);
                renderTree(solveId, res.data.nodes);
            } catch (e) { console.error("获取任务树失败", e); }
        };

        const buildTree = (rootId, nodes) => {
            const node = nodes.find(n => n.node_id === rootId);
            if (!node) return null;
            const childrenNodes = nodes.filter(n => n.parent_id === rootId && n.node_id !== rootId);
            
            let color = '#5470c6'; 
            if (node.status === 'completed') color = '#91cc75'; 
            if (node.status === 'failed' || node.status === 'error') color = '#ee6666'; 
            if (node.status === 'running') color = '#fac858'; 

            const realChildren = childrenNodes.map(c => buildTree(c.node_id, nodes)).filter(Boolean);

            return {
                name: node.skill_name || 'root',
                value: node.goal,
                status: node.status,
                cost: node.total_cost,
                latency: node.total_latency_s,
                attempts: node.attempts || [],
                itemStyle: { color: color },
                children: realChildren
            };
        };

        const renderTree = (rootId, nodes) => {
            if (!myChart) return;
            const treeData = buildTree(rootId, nodes);
            if (!treeData) return;

            const option = {
                tooltip: {
                    trigger: 'item',
                    triggerOn: 'mousemove',
                    enterable: true,
                    formatter: (params) => {
                        const d = params.data;
                        let html = `<div class="max-w-xs whitespace-normal break-words text-sm">
                            <b>🛠️ 节点:</b> ${d.name} (${d.status})<br/>
                            <b>总耗时:</b> ${d.latency ? d.latency.toFixed(2) : 0} s<br/>
                            <b>总花费:</b> ¥${d.cost ? d.cost.toFixed(6) : 0}<br/>
                            <hr class="my-1 border-gray-500" />
                            <b>任务目标:</b> ${d.value}
                        </div>`;
                        
                        if (d.attempts && d.attempts.length > 0) {
                            html += `<hr class="my-1 border-gray-500" /><div class="font-bold text-xs mb-1 text-gray-700">🔄 执行记录:</div>`;
                            html += `<div class="max-h-48 overflow-y-auto pr-1 no-scrollbar">`; 
                            d.attempts.forEach((attempt, idx) => {
                                const cost = attempt.cost ? attempt.cost.toFixed(6) : 0;
                                const latency = attempt.latency_s ? attempt.latency_s.toFixed(2) : 0;
                                const status = attempt.status || 'running';
                                const feedback = attempt.audit_feedback || '无/执行中...';
                                html += `<div class="mt-1 p-1.5 bg-gray-50 rounded text-xs border border-gray-200">
                                    <b>尝试 ${idx + 1}</b> <span class="text-gray-500">(${attempt.model || 'unknown'})</span> - <b>${status}</b><br/>
                                    <span class="text-gray-500">⏳ ${latency}s | 💰 ¥${cost}</span><br/>
                                    <span class="text-gray-700 break-words mt-0.5 inline-block">反馈: ${feedback}</span>
                                </div>`;
                            });
                            html += `</div>`;
                        }
                        return html;
                    }
                },
                series: [{
                    type: 'tree', data: [treeData],
                    top: '5%', left: '15%', bottom: '5%', right: '20%',
                    symbolSize: 14,
                    label: {
                        position: 'left', verticalAlign: 'middle', align: 'right',
                        formatter: (params) => {
                            const d = params.data;
                            const cost = d.cost ? `¥${d.cost.toFixed(4)}` : '¥0';
                            const time = d.latency ? `${d.latency.toFixed(1)}s` : '0s';
                            return `{name|${d.name}}\n{meta|⏳ ${time} | 💰 ${cost}}`;
                        },
                        rich: { name: { fontSize: 13, color: '#333', fontWeight: 'bold' }, meta: { fontSize: 11, color: '#666', paddingTop: 4 } }
                    },
                    leaves: { label: { position: 'right', verticalAlign: 'middle', align: 'left' } },
                    expandAndCollapse: true, animationDuration: 550, animationDurationUpdate: 750
                }]
            };
            myChart.setOption(option);
        };

        // ==========================
        // 3. 大模型配置逻辑
        // ==========================
        const fetchConfig = async () => {
            try {
                const res = await axios.get(`${API_BASE}/api/config/llm`);
                llmConfig.value = res.data;
            } catch (e) { console.error("获取配置失败", e); }
        };

        const updateDefault = async (role, modelRef) => {
            try {
                await axios.post(`${API_BASE}/api/config/llm/default`, { role, llm_ref: modelRef });
                alert(`${role} 默认模型已保存`);
            } catch (e) { alert("更新失败"); console.error(e); }
        };

        // --- Provider CRUD ---
        const openProviderModal = (name = '', configObj = null) => {
            isEditProv.value = !!name;
            if (configObj) {
                provForm.value = { name, config: JSON.parse(JSON.stringify(configObj)) };
            } else {
                provForm.value = { name: '', config: { base_url: '', api_key: '', api_type: 'openai-completions', models: [] } };
            }
            showProvModal.value = true;
        };

        const saveProvider = async () => {
            if (!provForm.value.name) return alert("Provider ID 不能为空");
            try {
                await axios.post(`${API_BASE}/api/config/llm/provider`, {
                    name: provForm.value.name,
                    config: provForm.value.config
                });
                showProvModal.value = false;
                fetchConfig();
            } catch (e) { alert("保存失败: " + (e.response?.data?.detail || e.message)); }
        };

        const deleteProvider = async (name) => {
            if (!confirm(`确定要删除供应商 [${name}] 及其所有模型吗？`)) return;
            try {
                await axios.delete(`${API_BASE}/api/config/llm/provider/${name}`);
                fetchConfig();
            } catch (e) { alert("删除失败"); }
        };

        // --- Model CRUD ---
        const openModelModal = (providerName, modelObj = null) => {
            currentProvForModel.value = providerName;
            isEditModel.value = !!modelObj;
            if (modelObj) {
                modelForm.value = JSON.parse(JSON.stringify(modelObj));
                if (!modelForm.value.cost) modelForm.value.cost = { input_1m: 0, output_1m: 0 };
            } else {
                modelForm.value = defaultModelForm();
            }
            showModelModal.value = true;
        };

        const saveModel = async () => {
            if (!modelForm.value.id) return alert("模型 ID 不能为空");
            try {
                await axios.post(`${API_BASE}/api/config/llm/model`, {
                    provider_name: currentProvForModel.value,
                    llm_config: modelForm.value
                });
                showModelModal.value = false;
                fetchConfig();
            } catch (e) { alert("保存失败: " + (e.response?.data?.detail || e.message)); }
        };

        const deleteModel = async (providerName, modelId) => {
            if (!confirm(`确定要删除模型 [${modelId}] 吗？`)) return;
            try {
                await axios.delete(`${API_BASE}/api/config/llm/model/${providerName}/${modelId}`);
                fetchConfig();
            } catch (e) { alert("删除失败"); }
        };

        // 生命周期绑定
        onMounted(() => {
            loadHistory();
            initWebSocket();
            fetchConfig();
            setTimeout(() => {
                initECharts(); // 先初始化空表盘
                fetchHistoryTasks(); // 再去拉取历史任务，拉完后会自动触发第一条任务的画图
            }, 500);
        });

        watch(currentTab, (newVal) => {
            if (newVal === 'chat' && myChart) setTimeout(() => myChart.resize(), 100);
        });

        return {
            currentTab, wsConnected, inputText, messages, sendMessage,
            historyTasks, activeSolveId, activeTaskName, fetchHistoryTasks, fetchTaskTree,
            llmConfig, updateDefault,
            showProvModal, isEditProv, provForm, openProviderModal, saveProvider, deleteProvider,
            showModelModal, isEditModel, currentProvForModel, modelForm, openModelModal, saveModel, deleteModel,
            messageOffset, hasMoreMessages, isLoadingHistory, loadHistory, getArtifactUrl
        };
    }
}).mount('#app');