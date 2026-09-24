"use client";

import { ArrowUp, Square } from "lucide-react";
import { useEffect, useImperativeHandle, useRef, type Ref } from "react";

import { Orb } from "@/components/ui";
import { cn } from "@/lib/cn";

export interface ComposerHandle {
  focus: () => void;
}

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
  disabledReason?: string;
  ref?: Ref<ComposerHandle>;
}

const MAX_CHARS = 2000;

export function Composer({ value, onChange, onSend, onStop, streaming, disabled, disabledReason, ref }: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => textareaRef.current?.focus() }), []);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

  const canSend = !disabled && !streaming && value.trim().length > 0;

  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
    >
      <div
        className={cn(
          "flex items-end gap-2 rounded-[2rem] bg-surface p-2 pl-5 shadow-float ring-1 ring-line transition-shadow focus-within:ring-2 focus-within:ring-focus/60",
          disabled && "opacity-70",
        )}
      >
        <label htmlFor="chat-input" className="sr-only">
          Message the assistant
        </label>
        <textarea
          id="chat-input"
          ref={textareaRef}
          rows={1}
          value={value}
          maxLength={MAX_CHARS}
          disabled={disabled}
          placeholder={disabled ? (disabledReason ?? "The assistant is unavailable") : "Ask about your benefits or claims"}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (canSend) onSend();
            }
          }}
          className="max-h-40 min-h-12 flex-1 resize-none bg-transparent py-3 text-base text-ink outline-none placeholder:text-muted disabled:cursor-not-allowed sm:text-lg"
        />
        {streaming ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop the answer"
            className="pill-soft grid size-12 shrink-0 place-items-center rounded-full text-ink"
          >
            <Square aria-hidden className="size-4 fill-current" strokeWidth={1.5} />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!canSend}
            aria-label="Send"
            className="shrink-0 rounded-full transition-[opacity,transform] active:scale-95 disabled:opacity-40"
          >
            <Orb size={48}>
              <ArrowUp aria-hidden className="size-5" strokeWidth={2} />
            </Orb>
          </button>
        )}
      </div>
    </form>
  );
}
