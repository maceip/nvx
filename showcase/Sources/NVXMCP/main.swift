import Foundation
import NVXCore

struct JSONRPCRequest: Decodable {
    let jsonrpc: String
    let id: AnyCodable?
    let method: String
    let params: AnyCodable?
}

enum AnyCodable: Codable, Sendable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case dictionary([String: AnyCodable])
    case array([AnyCodable])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let b = try? container.decode(Bool.self) {
            self = .bool(b)
        } else if let i = try? container.decode(Int.self) {
            self = .int(i)
        } else if let d = try? container.decode(Double.self) {
            self = .double(d)
        } else if let s = try? container.decode(String.self) {
            self = .string(s)
        } else if let dict = try? container.decode([String: AnyCodable].self) {
            self = .dictionary(dict)
        } else if let arr = try? container.decode([AnyCodable].self) {
            self = .array(arr)
        } else {
            self = .null
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let s): try container.encode(s)
        case .int(let i): try container.encode(i)
        case .double(let d): try container.encode(d)
        case .bool(let b): try container.encode(b)
        case .dictionary(let dict): try container.encode(dict)
        case .array(let arr): try container.encode(arr)
        case .null: try container.encodeNil()
        }
    }

    var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }

    var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    var doubleValue: Double? {
        if case .double(let d) = self { return d }
        if case .int(let i) = self { return Double(i) }
        return nil
    }

    var dictValue: [String: AnyCodable]? {
        if case .dictionary(let d) = self { return d }
        return nil
    }
}

func sendResponse(id: AnyCodable?, result: [String: Any]) {
    guard let id = id else { return }
    var response: [String: Any] = [
        "jsonrpc": "2.0",
        "result": result
    ]
    switch id {
    case .string(let s): response["id"] = s
    case .int(let i): response["id"] = i
    case .double(let d): response["id"] = d
    default: response["id"] = NSNull()
    }
    if let data = try? JSONSerialization.data(withJSONObject: response),
       let json = String(data: data, encoding: .utf8) {
        FileHandle.standardOutput.write(Data((json + "\n").utf8))
    }
}

func sendError(id: AnyCodable?, code: Int, message: String) {
    var response: [String: Any] = [
        "jsonrpc": "2.0",
        "error": [
            "code": code,
            "message": message
        ]
    ]
    if let id = id {
        switch id {
        case .string(let s): response["id"] = s
        case .int(let i): response["id"] = i
        case .double(let d): response["id"] = d
        default: response["id"] = NSNull()
        }
    } else {
        response["id"] = NSNull()
    }
    if let data = try? JSONSerialization.data(withJSONObject: response),
       let json = String(data: data, encoding: .utf8) {
        FileHandle.standardOutput.write(Data((json + "\n").utf8))
    }
}

let toolsList: [[String: Any]] = [
    [
        "name": "nvx_sandbox_exec",
        "description": "Execute a bash command inside a hardware-isolated NVX microVM sandbox on macOS with sub-second startup.",
        "inputSchema": [
            "type": "object",
            "properties": [
                "command": [
                    "type": "string",
                    "description": "The command or script to execute inside the sandbox."
                ],
                "use_warm_snapshot": [
                    "type": "boolean",
                    "description": "If true (default), resumes an existing warm snapshot for sub-200ms execution. If false, cold boots a clean guest."
                ],
                "timeout_seconds": [
                    "type": "number",
                    "description": "Execution timeout in seconds (default 30)."
                ]
            ],
            "required": ["command"]
        ]
    ],
    [
        "name": "nvx_status",
        "description": "Check the health, readiness, and artifact paths of the NVX hypervisor and microVM sandbox on this machine.",
        "inputSchema": [
            "type": "object",
            "properties": [:] as [String: Any]
        ]
    ],
    [
        "name": "nvx_snapshot_verify",
        "description": "Inspect and verify an NVX microVM snapshot directory (validates manifest, architecture, memory, and state).",
        "inputSchema": [
            "type": "object",
            "properties": [
                "snapshot_path": [
                    "type": "string",
                    "description": "Absolute path to the snapshot directory."
                ]
            ],
            "required": ["snapshot_path"]
        ]
    ],
    [
        "name": "nvx_snapshot_checkpoint",
        "description": "Create an instant, named copy-on-write APFS microVM snapshot checkpoint (< 10ms) that can be rolled back at any time.",
        "inputSchema": [
            "type": "object",
            "properties": [
                "name": [
                    "type": "string",
                    "description": "Unique tag/name for this checkpoint (e.g. 'clean-slate', 'before-migration')."
                ],
                "source_snapshot": [
                    "type": "string",
                    "description": "Optional path to the source snapshot (defaults to active warm snapshot)."
                ]
            ],
            "required": ["name"]
        ]
    ],
    [
        "name": "nvx_snapshot_rollback",
        "description": "Rollback and execute a command against a named microVM checkpoint in < 250ms.",
        "inputSchema": [
            "type": "object",
            "properties": [
                "checkpoint_name": [
                    "type": "string",
                    "description": "The named checkpoint to restore from."
                ],
                "command": [
                    "type": "string",
                    "description": "The command or script to execute in the rolled-back snapshot."
                ],
                "timeout_seconds": [
                    "type": "number",
                    "description": "Execution timeout in seconds (default 30)."
                ]
            ],
            "required": ["checkpoint_name", "command"]
        ]
    ],
    [
        "name": "nvx_snapshot_list",
        "description": "List all named microVM snapshot checkpoints available on this host.",
        "inputSchema": [
            "type": "object",
            "properties": [:] as [String: Any]
        ]
    ],
    [
        "name": "nvx_proxy_status",
        "description": "Check configuration and status of the host credential shield proxy.",
        "inputSchema": [
            "type": "object",
            "properties": [:] as [String: Any]
        ]
    ]
]

@main
struct MCPServer {
    static func main() async {
        let engine = NVXEngine.shared

        do {
            for try await line in FileHandle.standardInput.bytes.lines {
                let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !trimmed.isEmpty else { continue }
                guard let data = trimmed.data(using: .utf8) else { continue }

            guard let request = try? JSONDecoder().decode(JSONRPCRequest.self, from: data) else {
                sendError(id: nil, code: -32700, message: "Parse error")
                continue
            }

            switch request.method {
            case "initialize":
                sendResponse(id: request.id, result: [
                    "protocolVersion": "2024-11-05",
                    "capabilities": [
                        "tools": [:] as [String: Any]
                    ],
                    "serverInfo": [
                        "name": "nvx-mcp",
                        "version": "0.1.0"
                    ]
                ])

            case "notifications/initialized":
                // Client initialization notification: no response required
                break

            case "ping":
                sendResponse(id: request.id, result: [:] as [String: Any])

            case "tools/list":
                sendResponse(id: request.id, result: [
                    "tools": toolsList
                ])

            case "tools/call":
                let paramsDict = request.params?.dictValue
                let toolName = paramsDict?["name"]?.stringValue ?? ""
                let argsDict = paramsDict?["arguments"]?.dictValue ?? [:]

                switch toolName {
                case "nvx_sandbox_exec":
                    guard let cmd = argsDict["command"]?.stringValue else {
                        sendError(id: request.id, code: -32602, message: "Missing required argument 'command'")
                        break
                    }
                    let useWarm = argsDict["use_warm_snapshot"]?.boolValue ?? true
                    let timeout = argsDict["timeout_seconds"]?.doubleValue ?? 30.0

                    do {
                        let res = try await engine.runCommand(
                            command: cmd,
                            timeout: timeout,
                            preferWarmSnapshot: useWarm
                        )
                        let statusText = res.wasRestored
                            ? "Restored from warm snapshot in \(String(format: "%.1f", res.durationMs))ms"
                            : "Cold boot completed in \(String(format: "%.1f", res.durationMs))ms"
                        let text = "\(res.stdout)\n\n[Status: \(statusText), Exit Code: \(res.exitCode)]"
                        sendResponse(id: request.id, result: [
                            "content": [
                                [
                                    "type": "text",
                                    "text": text
                                ]
                            ],
                            "isError": !res.isSuccess
                        ])
                    } catch {
                        sendResponse(id: request.id, result: [
                            "content": [
                                [
                                    "type": "text",
                                    "text": "Execution error: \(error.localizedDescription)"
                                ]
                            ],
                            "isError": true
                        ])
                    }

                case "nvx_status":
                    let status = engine.checkStatus()
                    if let jsonData = try? JSONEncoder().encode(status),
                       let jsonText = String(data: jsonData, encoding: .utf8) {
                        sendResponse(id: request.id, result: [
                            "content": [
                                [
                                    "type": "text",
                                    "text": jsonText
                                ]
                            ],
                            "isError": false
                        ])
                    } else {
                        sendError(id: request.id, code: -32603, message: "Failed to encode status")
                    }

                case "nvx_snapshot_verify":
                    guard let path = argsDict["snapshot_path"]?.stringValue else {
                        sendError(id: request.id, code: -32602, message: "Missing required argument 'snapshot_path'")
                        break
                    }
                    let manifest = await engine.verifySnapshot(at: URL(fileURLWithPath: path))
                    if let jsonData = try? JSONEncoder().encode(manifest),
                       let jsonText = String(data: jsonData, encoding: .utf8) {
                        sendResponse(id: request.id, result: [
                            "content": [
                                [
                                    "type": "text",
                                    "text": jsonText
                                ]
                            ],
                            "isError": !manifest.isValid
                        ])
                    } else {
                        sendError(id: request.id, code: -32603, message: "Failed to encode manifest")
                    }

                case "nvx_snapshot_checkpoint":
                    guard let name = argsDict["name"]?.stringValue else {
                        sendError(id: request.id, code: -32602, message: "Missing required argument 'name'")
                        break
                    }
                    let sourcePath = argsDict["source_snapshot"]?.stringValue.map { URL(fileURLWithPath: $0) }
                    do {
                        let target = try engine.createCheckpoint(name: name, sourceSnapshot: sourcePath)
                        sendResponse(id: request.id, result: [
                            "content": [
                                [
                                    "type": "text",
                                    "text": "Checkpoint '\(name)' created successfully at \(target.path)"
                                ]
                            ],
                            "isError": false
                        ])
                    } catch {
                        sendResponse(id: request.id, result: [
                            "content": [
                                [
                                    "type": "text",
                                    "text": "Failed to create checkpoint: \(error.localizedDescription)"
                                ]
                            ],
                            "isError": true
                        ])
                    }

                case "nvx_snapshot_rollback":
                    guard let name = argsDict["checkpoint_name"]?.stringValue else {
                        sendError(id: request.id, code: -32602, message: "Missing required argument 'checkpoint_name'")
                        break
                    }
                    guard let cmd = argsDict["command"]?.stringValue else {
                        sendError(id: request.id, code: -32602, message: "Missing required argument 'command'")
                        break
                    }
                    let timeout = argsDict["timeout_seconds"]?.doubleValue ?? 30.0
                    let checkpointURL = engine.snapshotsDirectory.appending(path: name)
                    do {
                        let res = try await engine.runCommand(
                            command: cmd,
                            timeout: timeout,
                            preferWarmSnapshot: true,
                            explicitSnapshot: checkpointURL
                        )
                        let text = "\(res.stdout)\n\n[Status: Rolled back to checkpoint '\(name)' in \(String(format: "%.1f", res.durationMs))ms, Exit Code: \(res.exitCode)]"
                        sendResponse(id: request.id, result: [
                            "content": [
                                [
                                    "type": "text",
                                    "text": text
                                ]
                            ],
                            "isError": !res.isSuccess
                        ])
                    } catch {
                        sendResponse(id: request.id, result: [
                            "content": [
                                [
                                    "type": "text",
                                    "text": "Rollback failed: \(error.localizedDescription)"
                                ]
                            ],
                            "isError": true
                        ])
                    }

                case "nvx_snapshot_list":
                    let checkpoints = engine.listCheckpoints()
                    sendResponse(id: request.id, result: [
                        "content": [
                            [
                                "type": "text",
                                "text": "Available checkpoints (\(checkpoints.count)): \(checkpoints.joined(separator: ", "))"
                            ]
                        ],
                        "isError": false
                    ])

                case "nvx_proxy_status":
                    let hasAnthropic = ProcessInfo.processInfo.environment["ANTHROPIC_API_KEY"] != nil
                    let hasOpenAI = ProcessInfo.processInfo.environment["OPENAI_API_KEY"] != nil
                    let statusDict: [String: Any] = [
                        "anthropicConfigured": hasAnthropic,
                        "openAIConfigured": hasOpenAI,
                        "defaultPort": 18080,
                        "supportedEndpoints": ["https://api.anthropic.com/v1/messages", "https://api.openai.com/v1/chat/completions"]
                    ]
                    if let data = try? JSONSerialization.data(withJSONObject: statusDict, options: .prettyPrinted),
                       let str = String(data: data, encoding: .utf8) {
                        sendResponse(id: request.id, result: [
                            "content": [
                                [
                                    "type": "text",
                                    "text": str
                                ]
                            ],
                            "isError": false
                        ])
                    } else {
                        sendError(id: request.id, code: -32603, message: "Failed to format proxy status")
                    }

                default:
                    sendError(id: request.id, code: -32601, message: "Tool not found: \(toolName)")
                }

            default:
                sendError(id: request.id, code: -32601, message: "Method not found: \(request.method)")
            }
        }
        } catch {
            // EOF or pipe broken
        }
    }
}
