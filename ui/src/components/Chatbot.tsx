import React, { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { X, Send, User, Bot, Sparkles, Maximize2, Minimize2, FileText } from "lucide-react";
import { Input } from "@/components/ui/input";
import { ChatAnswerPayload } from "../lib/api";

interface Message {
  id: string;
  text: string;
  sender: "user" | "bot";
  citations?: ChatAnswerPayload["citations"];
}

const SUGGESTED_QUESTIONS = [
  "Is the property in a bushfire prone area?",
  "What planning overlays affect the property?",
  "Are there any outstanding rates or charges?",
  "What is the zoning of the property?",
  "Are there any easements or covenants?",
];

interface ChatbotProps {
  isOpen: boolean;
  onClose: () => void;
  onAsk: (question: string) => Promise<ChatAnswerPayload>;
}

export function Chatbot({ isOpen, onClose, onAsk }: ChatbotProps) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "1",
      text: "Hello! I'm your Convey Agent AI assistant. Ask any document question and I'll answer with file and page citations where available.",
      sender: "bot",
    },
  ]);
  const [inputValue, setInputValue] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-scroll to bottom when messages change or typing indicator appears
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isSending]);

  // Focus input when chatbot opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 300);
    } else {
      setInputValue("");
    }
  }, [isOpen]);

  const handleSend = async (text?: string) => {
    const next = (text ?? inputValue).trim();
    if (!next || isSending) return;

    const userMessage: Message = {
      id: `${Date.now()}-user`,
      text: next,
      sender: "user",
    };
    setMessages((prev) => [...prev, userMessage]);
    setInputValue("");
    setIsSending(true);
    try {
      const answer = await onAsk(next);
      const botMessage: Message = {
        id: `${Date.now()}-bot`,
        text: answer.answer || "I could not find a precise answer in the uploaded documents.",
        sender: "bot",
        citations: answer.citations,
      };
      setMessages((prev) => [...prev, botMessage]);
    } catch (error) {
      const botMessage: Message = {
        id: `${Date.now()}-bot-error`,
        text: error instanceof Error ? error.message : "I couldn't answer that from the current run.",
        sender: "bot",
      };
      setMessages((prev) => [...prev, botMessage]);
    } finally {
      setIsSending(false);
    }
  };

  const panelWidth = expanded ? 600 : 400;

  return (
    <motion.div
      initial={false}
      animate={{
        width: isOpen ? panelWidth : 0,
        opacity: isOpen ? 1 : 0,
      }}
      transition={{ type: "spring", stiffness: 300, damping: 30 }}
      className="h-full border-l bg-white flex flex-col overflow-hidden shadow-2xl relative z-10 shrink-0"
    >
      {/* Header */}
      <div className="h-16 border-b flex items-center justify-between px-5 bg-slate-50/50 shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-slate-900">AI Assistant</h3>
            <div className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              <span className="text-[10px] text-slate-400 font-medium uppercase tracking-wider">Online</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setExpanded((e) => !e)}
            className="p-2 hover:bg-slate-200 rounded-lg text-slate-400 transition-colors"
            title={expanded ? "Collapse panel" : "Expand panel"}
          >
            {expanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
          </button>
          <button onClick={onClose} className="p-2 hover:bg-slate-200 rounded-lg text-slate-400 transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-5 space-y-5 custom-scrollbar">
        {messages.map((message) => (
          <div key={message.id} className={`flex ${message.sender === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`flex gap-2.5 max-w-[88%] ${message.sender === "user" ? "flex-row-reverse" : ""}`}>
              <div className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-1 ${message.sender === "user" ? "bg-slate-100" : "bg-primary/10"}`}>
                {message.sender === "user" ? <User className="w-3.5 h-3.5 text-slate-500" /> : <Bot className="w-3.5 h-3.5 text-primary" />}
              </div>
              <div className={`p-3.5 rounded-2xl text-[0.85rem] leading-relaxed ${message.sender === "user" ? "bg-primary text-white rounded-tr-none shadow-md shadow-primary/10" : "bg-slate-100 text-slate-700 rounded-tl-none"}`}>
                <p className="whitespace-pre-wrap">{message.text}</p>
                {message.sender === "bot" && message.citations && message.citations.length > 0 ? (
                  <div className="mt-3 space-y-1.5">
                    <p className="text-[0.7rem] font-semibold uppercase tracking-wider text-slate-500 mb-1">Sources</p>
                    {message.citations.map((citation, index) => (
                      <div key={`${message.id}-${index}`} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-[0.75rem] text-slate-600">
                        <p className="font-semibold text-slate-700 flex items-center gap-1.5">
                          <FileText className="w-3 h-3 text-primary/60" />
                          {citation.file || "Unknown file"}{citation.page ? ` — p.${citation.page}` : ""}
                        </p>
                        {citation.quote ? <p className="mt-1 whitespace-pre-wrap text-slate-500 italic">{citation.quote}</p> : null}
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ))}

        {/* Typing indicator */}
        {isSending ? (
          <div className="flex justify-start">
            <div className="flex gap-2.5 max-w-[88%]">
              <div className="w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-1 bg-primary/10">
                <Bot className="w-3.5 h-3.5 text-primary" />
              </div>
              <div className="p-3.5 rounded-2xl rounded-tl-none bg-slate-100 text-slate-700">
                <div className="flex items-center gap-1.5 px-2">
                  <span className="h-2 w-2 rounded-full bg-slate-400 animate-bounce [animation-delay:-0.2s]" />
                  <span className="h-2 w-2 rounded-full bg-slate-400 animate-bounce [animation-delay:-0.1s]" />
                  <span className="h-2 w-2 rounded-full bg-slate-400 animate-bounce" />
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {/* Scroll anchor */}
        <div ref={messagesEndRef} />
      </div>

      {/* Suggested questions — show only at the start */}
      {messages.length <= 1 && !isSending ? (
        <div className="px-4 pb-3 shrink-0">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-2 px-1">Suggested questions</p>
          <div className="flex flex-wrap gap-1.5">
            {SUGGESTED_QUESTIONS.map((q) => (
              <button
                key={q}
                onClick={() => handleSend(q)}
                className="text-[0.75rem] px-3 py-1.5 rounded-full border border-slate-200 bg-white text-slate-600 hover:border-primary hover:text-primary transition-colors whitespace-nowrap"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {/* Input */}
      <div className="p-4 border-t bg-white shrink-0">
        <div className="relative">
          <Input
            ref={inputRef}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder="Ask anything about the documents..."
            className="pr-12 h-12 bg-slate-50 border-transparent focus:bg-white focus:border-primary rounded-xl"
          />
          <button
            onClick={() => handleSend()}
            disabled={!inputValue.trim() || isSending}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-lg bg-primary text-white disabled:opacity-50 transition-all hover:scale-105 active:scale-95"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
        <p className="text-[10px] text-slate-400 text-center mt-2">
          Answers sourced from uploaded documents. Always verify important information.
        </p>
      </div>

      <style>{`
        .custom-scrollbar::-webkit-scrollbar { width: 4px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 10px; }
      `}</style>
    </motion.div>
  );
}
