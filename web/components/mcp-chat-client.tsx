"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";

type Message = { from: "user" | "bot"; text: string };
type Provider = "openai" | "local";
type ToolSet = "all" | "scrna" | "cyl" | "generic" | "";

interface MCPTool {
  name: string;
  description: string;
}

interface ThreadInfo {
  thread_id: string;
  title: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface LLMSettings {
  provider: Provider;
  model: string;
  toolSet: ToolSet;
  mcpToolNames: string[];
}

const TOOL_SET_OPTIONS: { value: ToolSet; label: string; description: string }[] = [
  { value: "scrna", label: "Single-Cell RNA", description: "Datasets, genes, clusters, DE analysis" },
  { value: "cyl", label: "Phenotyping", description: "Experiments, plants, scans, traits" },
  { value: "all", label: "All Tools", description: "Full access to all data types" },
  { value: "generic", label: "Generic", description: "Basic database queries only" },
];

const AVAILABLE_MODELS: Record<Provider, string[]> = {
  openai: ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
  local: ["Qwen/Qwen3-8B"],
};

const PROVIDER_LABELS: Record<Provider, string> = {
  openai: "OpenAI",
  local: "Local LLM",
};

const SETTINGS_KEY = "mcp_llm_settings";
const THREAD_KEY = "mcp_chat_thread_id";

// Per-thread localStorage for messages
function messagesKey(threadId: string): string {
  return `mcp_chat_messages_${threadId}`;
}

function loadMessagesForThread(threadId: string): Message[] {
  try {
    const raw = localStorage.getItem(messagesKey(threadId));
    if (!raw) return [];
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

function saveMessagesForThread(threadId: string, messages: Message[]) {
  try {
    localStorage.setItem(messagesKey(threadId), JSON.stringify(messages));
  } catch {}
}

function removeMessagesForThread(threadId: string) {
  try {
    localStorage.removeItem(messagesKey(threadId));
  } catch {}
}

function generateThreadId(): string {
  return crypto.randomUUID();
}

function loadThreadId(): string {
  try {
    const id = localStorage.getItem(THREAD_KEY);
    if (id) return id;
  } catch {}
  const newId = generateThreadId();
  saveThreadId(newId);
  return newId;
}

function saveThreadId(id: string) {
  try {
    localStorage.setItem(THREAD_KEY, id);
  } catch {}
}

function loadSettings(): LLMSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return { provider: "local", model: "Qwen/Qwen3-8B", toolSet: "generic", mcpToolNames: [] };
    const parsed = JSON.parse(raw);
    if (!parsed.toolSet) parsed.toolSet = "generic";
    if (!parsed.mcpToolNames) parsed.mcpToolNames = [];
    // Migrate old settings that had claude
    if (parsed.provider === "claude") parsed.provider = "local";
    return parsed;
  } catch {
    return { provider: "local", model: "Qwen/Qwen3-8B", toolSet: "generic", mcpToolNames: [] };
  }
}

function saveSettings(settings: LLMSettings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {}
}

async function getAuthToken(): Promise<string | null> {
  try {
    const supabase = createClientSupabaseClient();
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token || null;
  } catch {
    return null;
  }
}

function relativeTime(dateStr: string | null): string {
  if (!dateStr) return "";
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay === 1) return "yesterday";
  if (diffDay < 7) return `${diffDay}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

const API_BASE_URL = ((process.env.NEXT_PUBLIC_MCP_URL as string) || "http://localhost:5002").replace(/\/$/, "");

export default function MCPChat() {
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeThreadId, setActiveThreadId] = useState<string>("default");

  // Thread history
  const [threads, setThreads] = useState<ThreadInfo[]>([]);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);

  // MCP Tools collapsible
  const [mcpToolsCollapsed, setMcpToolsCollapsed] = useState(true);

  // LLM Settings
  const [settings, setSettings] = useState<LLMSettings>(() => ({
    provider: "local",
    model: "Qwen/Qwen3-8B",
    toolSet: "generic",
    mcpToolNames: [],
  }));
  const [availableMcpTools, setAvailableMcpTools] = useState<MCPTool[]>([]);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  // Fetch thread list
  const fetchThreads = useCallback(async () => {
    try {
      const token = await getAuthToken();
      if (!token) return;
      const resp = await fetch(`${API_BASE_URL}/langchain/threads`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (resp.ok) {
        const data = await resp.json();
        setThreads(data.threads || []);
      }
    } catch {}
  }, []);

  useEffect(() => {
    try {
      const s = loadSettings();
      setSettings(s);
    } catch {}

    // Load persisted thread ID and messages for that thread
    const savedThreadId = loadThreadId();
    setActiveThreadId(savedThreadId);
    const savedMessages = loadMessagesForThread(savedThreadId);
    if (savedMessages.length > 0) setMessages(savedMessages);

    // Fetch available MCP tools from backend
    fetch(`${API_BASE_URL}/langchain/mcp-tools`)
      .then((r) => r.json())
      .then((data) => {
        const HIDDEN_TOOLS = new Set([
          "list_available_experiments",
          "load_experiment_data",
          "inspect_data_quality",
        ]);
        setAvailableMcpTools((data.tools || []).filter((t: MCPTool) => !HIDDEN_TOOLS.has(t.name)));
      })
      .catch(() => setAvailableMcpTools([]));

    // Fetch thread history
    fetchThreads();
  }, [fetchThreads]);

  // Persist messages to localStorage whenever they change (per-thread)
  useEffect(() => {
    if (activeThreadId && activeThreadId !== "default") {
      saveMessagesForThread(activeThreadId, messages);
    }
  }, [messages, activeThreadId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function handleProviderChange(provider: Provider) {
    const newModel = AVAILABLE_MODELS[provider][0];
    const newSettings = { ...settings, provider, model: newModel };
    setSettings(newSettings);
    saveSettings(newSettings);
  }

  function handleModelChange(model: string) {
    const newSettings = { ...settings, model };
    setSettings(newSettings);
    saveSettings(newSettings);
  }

  function handleToolSetChange(toolSet: ToolSet) {
    const newToolSet = settings.toolSet === toolSet ? "" as ToolSet : toolSet;
    const newSettings = { ...settings, toolSet: newToolSet };
    setSettings(newSettings);
    saveSettings(newSettings);
  }

  function handleToggleMcpTool(toolName: string) {
    const current = settings.mcpToolNames || [];
    const updated = current.includes(toolName)
      ? current.filter((n) => n !== toolName)
      : [...current, toolName];
    const newSettings = { ...settings, mcpToolNames: updated };
    setSettings(newSettings);
    saveSettings(newSettings);
  }

  function handleNewChat() {
    // Save current messages before switching
    if (activeThreadId && activeThreadId !== "default") {
      saveMessagesForThread(activeThreadId, messages);
    }
    const newThreadId = generateThreadId();
    setActiveThreadId(newThreadId);
    saveThreadId(newThreadId);
    setMessages([]);
    // Refresh thread list so it stays current
    fetchThreads();
  }

  function handleSelectThread(threadId: string) {
    if (threadId === activeThreadId) return;
    // Save current messages before switching
    if (activeThreadId && activeThreadId !== "default") {
      saveMessagesForThread(activeThreadId, messages);
    }
    // Switch to selected thread
    setActiveThreadId(threadId);
    saveThreadId(threadId);
    const savedMessages = loadMessagesForThread(threadId);
    setMessages(savedMessages);
  }

  async function handleDeleteThread(threadId: string) {
    try {
      const token = await getAuthToken();
      if (!token) return;
      await fetch(`${API_BASE_URL}/langchain/threads/${threadId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      // Remove from local list
      setThreads((prev) => prev.filter((t) => t.thread_id !== threadId));
      // Remove localStorage messages
      removeMessagesForThread(threadId);
      // If deleted thread was active, start new chat
      if (threadId === activeThreadId) {
        handleNewChat();
      }
    } catch {}
  }

  function sendPrompt() {
    if (!prompt.trim()) return;
    const text = prompt.trim();
    setMessages((m) => [...m, { from: "user", text }]);
    setPrompt("");
    startRequest(text);
  }

  function startRequest(text: string) {
    setStreaming(true);
    setMessages((m) => [...m, { from: "bot", text: "" }]);
    const url = `${API_BASE_URL}/langchain/chat`;

    (async () => {
      try {
        const body: any = {
          prompt: text,
          provider: settings.provider,
          model: settings.model,
          tool_set: settings.toolSet || "generic",
          mcp_tool_names: settings.mcpToolNames || [],
          thread_id: activeThreadId,
        };

        const token = await getAuthToken();
        if (!token) {
          throw new Error("Please log in to use the Bloom Assistant.");
        }

        const resp = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${token}`,
          },
          body: JSON.stringify(body),
        });
        const rawBody = await resp.text();
        if (!resp.ok) {
          throw new Error(`Agent request failed ${resp.status}: ${rawBody}`);
        }
        let data: any = null;
        try {
          data = JSON.parse(rawBody);
        } catch {
          // rawBody already captured above
        }

        const parts: string[] = [];

        if (data && data.answer) {
          parts.push(data.answer);
        }

        if (data && data.tools_used && data.tools_used.length > 0) {
          parts.push("\nTools: " + data.tools_used.join(", "));
        }

        if (data && data.error) {
          parts.push(`Error: ${data.error}`);
          if (data.detail) {
            parts.push(`Details: ${data.detail}`);
          }
        }

        if (rawBody) {
          parts.push("--- Raw response body (non-JSON) ---\n" + rawBody);
        }

        const textOut = parts.join("\n\n");
        setMessages((msgs) => {
          const copy = [...msgs];
          const last = copy.slice(-1)[0];
          if (last && last.from === "bot") {
            copy[copy.length - 1] = { from: "bot", text: textOut };
          } else {
            copy.push({ from: "bot", text: textOut });
          }
          return copy;
        });

        // Refresh thread list after message (new thread may appear, timestamps update)
        fetchThreads();
      } catch (err: any) {
        const errMsg = `Error contacting agent: ${err?.message ?? String(err)}`;
        setMessages((msgs) => {
          const copy = [...msgs];
          const last = copy.slice(-1)[0];
          if (last && last.from === "bot") {
            copy[copy.length - 1] = { from: "bot", text: errMsg };
          } else {
            copy.push({ from: "bot", text: errMsg });
          }
          return copy;
        });
      } finally {
        setStreaming(false);
      }
    })();
  }

  // Check if a resumed thread has no localStorage messages (show fallback)
  const isResumedWithoutMessages = messages.length === 0 && threads.some((t) => t.thread_id === activeThreadId);
  const resumedThread = threads.find((t) => t.thread_id === activeThreadId);

  return (
    <div style={{ display: "flex", height: "100%", width: "100%", background: "#f8fafc" }}>
      {/* Sidebar */}
      <div
        style={{
          width: sidebarCollapsed ? 0 : 340,
          minWidth: sidebarCollapsed ? 0 : 340,
          borderRight: sidebarCollapsed ? "none" : "1px solid #e2e8f0",
          background: "white",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          transition: "width 0.2s, min-width 0.2s",
        }}
      >
        {/* Sidebar Header */}
        <div
          style={{
            padding: "20px 20px 16px",
            borderBottom: "1px solid #e2e8f0",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: "linear-gradient(135deg, #0ea5a4 0%, #0d9695 100%)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 18,
                color: "white",
              }}
            >
              {"\u{1F331}"}
            </div>
            <div>
              <div style={{ fontWeight: 600, fontSize: 16, color: "#1e293b" }}>Bloom Assistant</div>
              <div style={{ fontSize: 12, color: "#94a3b8" }}>
                {PROVIDER_LABELS[settings.provider]} &middot; {settings.model.split("/").pop()}
              </div>
            </div>
          </div>
        </div>

        {/* Sidebar Content - Scrollable */}
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px" }}>
          {/* Provider Section */}
          <div style={{ marginBottom: 20 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: "#94a3b8",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                marginBottom: 10,
              }}
            >
              LLM Provider
            </div>
            <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
              {(["local", "openai"] as Provider[]).map((p) => (
                <button
                  key={p}
                  onClick={() => handleProviderChange(p)}
                  style={{
                    flex: 1,
                    padding: "8px 12px",
                    border: settings.provider === p ? "2px solid #0ea5a4" : "2px solid #e2e8f0",
                    borderRadius: 8,
                    background: settings.provider === p ? "#f0fdfa" : "white",
                    cursor: "pointer",
                    fontSize: 13,
                    fontWeight: 500,
                    color: settings.provider === p ? "#0ea5a4" : "#64748b",
                    transition: "all 0.2s",
                  }}
                >
                  {PROVIDER_LABELS[p]}
                  {p === "local" && (
                    <span style={{ fontSize: 10, marginLeft: 4, opacity: 0.7 }}>(Free)</span>
                  )}
                </button>
              ))}
            </div>

            {/* Model Selector */}
            <select
              value={settings.model}
              onChange={(e) => handleModelChange(e.target.value)}
              style={{
                width: "100%",
                padding: "8px 10px",
                border: "2px solid #e2e8f0",
                borderRadius: 8,
                fontSize: 13,
                background: "white",
                cursor: "pointer",
                outline: "none",
                color: "#334155",
              }}
            >
              {AVAILABLE_MODELS[settings.provider].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>

            {settings.provider === "openai" && (
              <div
                style={{
                  marginTop: 10,
                  padding: 10,
                  background: "#eff6ff",
                  borderRadius: 8,
                  fontSize: 11,
                  color: "#1d4ed8",
                }}
              >
                OpenAI API key is configured on the server.
              </div>
            )}

            {settings.provider === "local" && (
              <div
                style={{
                  marginTop: 10,
                  padding: 10,
                  background: "#ecfdf5",
                  borderRadius: 8,
                  fontSize: 11,
                  color: "#047857",
                }}
              >
                Local LLM runs on the server — no API key needed.
              </div>
            )}
          </div>

          {/* Data Focus Section */}
          <div style={{ marginBottom: 20 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: "#94a3b8",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                marginBottom: 10,
              }}
            >
              Data Focus
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
              {TOOL_SET_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => handleToolSetChange(opt.value)}
                  style={{
                    padding: "8px 10px",
                    border: settings.toolSet === opt.value ? "2px solid #0ea5a4" : "2px solid #e2e8f0",
                    borderRadius: 8,
                    background: settings.toolSet === opt.value ? "#f0fdfa" : "white",
                    cursor: "pointer",
                    textAlign: "left",
                    transition: "all 0.2s",
                  }}
                >
                  <div
                    style={{
                      fontSize: 12,
                      fontWeight: 500,
                      color: settings.toolSet === opt.value ? "#0ea5a4" : "#334155",
                    }}
                  >
                    {opt.label}
                  </div>
                  <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>
                    {opt.description}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Chat History Section — Collapsible */}
          <div style={{ marginBottom: 20 }}>
            <button
              onClick={() => setHistoryCollapsed((c) => !c)}
              style={{
                width: "100%",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                background: "none",
                border: "none",
                cursor: "pointer",
                padding: 0,
                marginBottom: historyCollapsed ? 0 : 10,
              }}
            >
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#94a3b8",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                Chat History
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                {threads.length > 0 && (
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 600,
                      color: "white",
                      background: "#94a3b8",
                      borderRadius: 10,
                      padding: "1px 7px",
                    }}
                  >
                    {threads.length}
                  </span>
                )}
                <span style={{ fontSize: 12, color: "#94a3b8", transition: "transform 0.2s", display: "inline-block", transform: historyCollapsed ? "rotate(-90deg)" : "rotate(0deg)" }}>
                  {"\u25BE"}
                </span>
              </span>
            </button>
            {!historyCollapsed && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {threads.length === 0 && (
                  <div style={{ fontSize: 12, color: "#94a3b8", padding: "8px 0" }}>
                    No conversations yet
                  </div>
                )}
                {threads.map((thread) => {
                  const isActive = thread.thread_id === activeThreadId;
                  return (
                    <div
                      key={thread.thread_id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "8px 10px",
                        borderRadius: 8,
                        background: isActive ? "#f0fdfa" : "transparent",
                        border: isActive ? "1px solid #0ea5a4" : "1px solid transparent",
                        cursor: "pointer",
                        transition: "all 0.15s",
                      }}
                      onClick={() => handleSelectThread(thread.thread_id)}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div
                          style={{
                            fontSize: 13,
                            fontWeight: isActive ? 600 : 400,
                            color: isActive ? "#0ea5a4" : "#334155",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {thread.title || "Untitled"}
                        </div>
                        <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>
                          {relativeTime(thread.updated_at)}
                        </div>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteThread(thread.thread_id);
                        }}
                        style={{
                          background: "none",
                          border: "none",
                          cursor: "pointer",
                          fontSize: 14,
                          color: "#cbd5e1",
                          padding: "2px 4px",
                          borderRadius: 4,
                          flexShrink: 0,
                          transition: "color 0.15s",
                        }}
                        onMouseEnter={(e) => (e.currentTarget.style.color = "#ef4444")}
                        onMouseLeave={(e) => (e.currentTarget.style.color = "#cbd5e1")}
                        title="Delete thread"
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="3 6 5 6 21 6" />
                          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                          <line x1="10" y1="11" x2="10" y2="17" />
                          <line x1="14" y1="11" x2="14" y2="17" />
                        </svg>
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* MCP Tools Section — Collapsible */}
          {availableMcpTools.length > 0 && (
            <div>
              <button
                onClick={() => setMcpToolsCollapsed((c) => !c)}
                style={{
                  width: "100%",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  padding: 0,
                  marginBottom: mcpToolsCollapsed ? 0 : 10,
                }}
              >
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: "#94a3b8",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                  }}
                >
                  MCP Tools
                </span>
                <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  {(settings.mcpToolNames || []).length > 0 && (
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 600,
                        color: "white",
                        background: "#0ea5a4",
                        borderRadius: 10,
                        padding: "1px 7px",
                      }}
                    >
                      {settings.mcpToolNames.length} active
                    </span>
                  )}
                  <span style={{ fontSize: 12, color: "#94a3b8", transition: "transform 0.2s", display: "inline-block", transform: mcpToolsCollapsed ? "rotate(-90deg)" : "rotate(0deg)" }}>
                    {"\u25BE"}
                  </span>
                </span>
              </button>
              {!mcpToolsCollapsed && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {availableMcpTools.map((tool) => {
                    const isActive = (settings.mcpToolNames || []).includes(tool.name);
                    return (
                      <button
                        key={tool.name}
                        onClick={() => handleToggleMcpTool(tool.name)}
                        style={{
                          display: "flex",
                          alignItems: "flex-start",
                          gap: 10,
                          padding: "10px 12px",
                          border: isActive ? "2px solid #0ea5a4" : "2px solid #e2e8f0",
                          borderRadius: 8,
                          background: isActive ? "#f0fdfa" : "white",
                          cursor: "pointer",
                          textAlign: "left",
                          transition: "all 0.2s",
                        }}
                      >
                        <span
                          style={{
                            width: 18,
                            height: 18,
                            borderRadius: 4,
                            border: isActive ? "none" : "2px solid #cbd5e1",
                            background: isActive ? "#0ea5a4" : "transparent",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontSize: 11,
                            color: "white",
                            flexShrink: 0,
                            marginTop: 1,
                          }}
                        >
                          {isActive ? "\u2713" : ""}
                        </span>
                        <div style={{ flex: 1 }}>
                          <div
                            style={{
                              fontSize: 13,
                              fontWeight: 500,
                              color: isActive ? "#0ea5a4" : "#334155",
                            }}
                          >
                            {tool.name}
                          </div>
                          <div style={{ fontSize: 11, color: "#64748b", marginTop: 2, lineHeight: 1.4 }}>
                            {tool.description}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

        </div>
      </div>

      {/* Main Chat Area */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* Chat Header */}
        <div
          style={{
            padding: "16px 24px",
            borderBottom: "1px solid #e2e8f0",
            background: "white",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button
              onClick={() => setSidebarCollapsed((s) => !s)}
              style={{
                border: "none",
                background: "none",
                cursor: "pointer",
                fontSize: 18,
                color: "#64748b",
                padding: "4px 8px",
                borderRadius: 6,
              }}
              title={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
            >
              {sidebarCollapsed ? "\u2630" : "\u2190"}
            </button>
            <span style={{ fontWeight: 600, fontSize: 16, color: "#1e293b" }}>Bloom Assistant</span>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "#94a3b8" }}>
              {PROVIDER_LABELS[settings.provider]} &middot; {settings.model.split("/").pop()}
            </span>
            <button
              onClick={handleNewChat}
              style={{
                border: "1px solid #e2e8f0",
                background: "white",
                cursor: "pointer",
                padding: "6px 14px",
                borderRadius: 8,
                fontSize: 13,
                color: "#64748b",
                fontWeight: 500,
                transition: "all 0.2s",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              + New Chat
            </button>
          </div>
        </div>

        {/* Messages Area */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "24px 24px",
          }}
        >
          <div style={{ maxWidth: 800, margin: "0 auto" }}>
            {/* Empty state: no messages and not a resumed thread */}
            {messages.length === 0 && !isResumedWithoutMessages && (
              <div
                style={{
                  textAlign: "center",
                  color: "#94a3b8",
                  padding: "80px 20px",
                  fontSize: 14,
                }}
              >
                <div style={{ fontSize: 48, marginBottom: 20 }}>{"\u{1F52C}"}</div>
                <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 18, color: "#64748b" }}>
                  How can I help you?
                </div>
                <div style={{ fontSize: 14, maxWidth: 400, margin: "0 auto", lineHeight: 1.6 }}>
                  Ask about datasets, gene expression, phenotyping scans, or run analysis with the
                  plug-in tools selected in the sidebar.
                </div>
              </div>
            )}
            {/* Resumed thread without localStorage — show fallback */}
            {isResumedWithoutMessages && resumedThread && (
              <div
                style={{
                  textAlign: "center",
                  color: "#64748b",
                  padding: "60px 20px",
                  fontSize: 14,
                }}
              >
                <div style={{ fontSize: 40, marginBottom: 16 }}>{"\u{1F4AC}"}</div>
                <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 16, color: "#334155" }}>
                  {resumedThread.title || "Previous conversation"}
                </div>
                <div
                  style={{
                    fontSize: 13,
                    maxWidth: 420,
                    margin: "0 auto",
                    lineHeight: 1.6,
                    padding: "12px 16px",
                    background: "#f0fdfa",
                    borderRadius: 10,
                    border: "1px solid #99e0df",
                    color: "#0d7c7b",
                  }}
                >
                  Agent remembers this conversation. No localStorage available.
                  Continue where you left off.
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} style={{ marginBottom: 20 }}>
                <div
                  style={{
                    fontSize: 12,
                    color: "#64748b",
                    marginBottom: 8,
                    fontWeight: 500,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <span style={{ fontSize: 16 }}>
                    {m.from === "user" ? "\u{1F464}" : "\u{1F331}"}
                  </span>
                  {m.from === "user" ? "You" : "Bloom"}
                </div>
                <div
                  style={{
                    background: m.from === "user" ? "#0ea5a4" : "white",
                    color: m.from === "user" ? "white" : "#1e293b",
                    padding: "14px 18px",
                    borderRadius: m.from === "user" ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
                    whiteSpace: "pre-wrap",
                    fontSize: 14,
                    lineHeight: 1.6,
                    boxShadow:
                      m.from === "user" ? "none" : "0 2px 8px rgba(0,0,0,0.06)",
                    border: m.from === "user" ? "none" : "1px solid #e2e8f0",
                    maxWidth: m.from === "user" ? "75%" : "100%",
                    marginLeft: m.from === "user" ? "auto" : 0,
                  }}
                >
                  {m.text}
                </div>
              </div>
            ))}
            {streaming && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "12px 0",
                }}
              >
                <span
                  style={{
                    width: 20,
                    height: 20,
                    border: "3px solid rgba(0,0,0,0.12)",
                    borderTopColor: "#0ea5a4",
                    borderRadius: "50%",
                    display: "inline-block",
                    animation: "mcp-spin 0.9s linear infinite",
                  }}
                />
                <span style={{ color: "#64748b", fontSize: 14 }}>Thinking...</span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input Area */}
        <div
          style={{
            padding: "16px 24px",
            borderTop: "1px solid #e2e8f0",
            background: "white",
          }}
        >
          <div style={{ maxWidth: 800, margin: "0 auto", display: "flex", gap: 12, alignItems: "flex-end" }}>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendPrompt();
                }
              }}
              placeholder="Ask about datasets, genes, or run analysis..."
              style={{
                flex: 1,
                padding: "14px 16px",
                borderRadius: 12,
                border: "2px solid #e2e8f0",
                fontSize: 15,
                resize: "none",
                minHeight: 52,
                maxHeight: 120,
                fontFamily: "inherit",
                outline: "none",
                transition: "border-color 0.2s",
              }}
              onFocus={(e) => (e.currentTarget.style.borderColor = "#0ea5a4")}
              onBlur={(e) => (e.currentTarget.style.borderColor = "#e2e8f0")}
              disabled={streaming}
              rows={1}
            />
            <button
              onClick={sendPrompt}
              disabled={streaming || !prompt.trim()}
              style={{
                background:
                  streaming || !prompt.trim()
                    ? "#94d3d1"
                    : "linear-gradient(135deg, #0ea5a4 0%, #0d9695 100%)",
                color: "white",
                border: "none",
                padding: "14px 28px",
                borderRadius: 12,
                fontSize: 15,
                fontWeight: 600,
                cursor: streaming || !prompt.trim() ? "not-allowed" : "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
                transition: "transform 0.2s, box-shadow 0.2s",
                boxShadow: "0 4px 12px rgba(14, 165, 164, 0.3)",
              }}
            >
              {streaming ? (
                <span
                  style={{
                    width: 18,
                    height: 18,
                    border: "2px solid rgba(255,255,255,0.3)",
                    borderTopColor: "white",
                    borderRadius: "50%",
                    display: "inline-block",
                    animation: "mcp-spin 0.9s linear infinite",
                  }}
                />
              ) : (
                "Send"
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Spinner animation */}
      <style>{`
        @keyframes mcp-spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
