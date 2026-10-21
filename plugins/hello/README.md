# hello

热加载演示插件。用来验证“新增能力不重启进程”这条架构约束。

## 提供

- 工具 `hello_ping`
- HTTP op：`POST /api/plugin/hello/ping`
- 钩子：`kernel.started`

## 验证热替换

```bash
curl -s -X POST http://127.0.0.1:8088/api/plugin/hello/ping \
  -H "Authorization: Bearer $AGENT_WEB_TOKEN" \
  -H "Content-Type: application/json" -d '{}'

# 修改本目录的 plugin.py 或 plugin.json 后
curl -s -X POST http://127.0.0.1:8088/api/plugins/hello/reload \
  -H "Authorization: Bearer $AGENT_WEB_TOKEN"
```

也可以设置 `AGENT_PLUGIN_AUTORELOAD=1` 让内核监听文件变化。

## 状态

`experimental`，可随时删除，不影响主流程。
