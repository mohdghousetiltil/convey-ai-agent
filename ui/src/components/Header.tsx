import React from "react";
import { ChevronLeft, User, LogOut, Settings, Shield, MessageSquare } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface HeaderProps {
  onBack?: () => void;
  onProfile?: () => void;
  onSettings?: () => void;
  onPolicy?: () => void;
  onLogout?: () => void;
  userInitials?: string;
  isChatOpen?: boolean;
  onChatToggle?: () => void;
  title?: string;
  showChatToggle?: boolean;
}

export function Header({
  onBack,
  onProfile,
  onSettings,
  onPolicy,
  onLogout,
  userInitials = "JD",
  isChatOpen,
  onChatToggle,
  title = "Convey Agent",
  showChatToggle = false,
}: HeaderProps) {
  return (
    <header className="sticky top-0 z-50 flex h-16 shrink-0 items-center justify-between border-b border-border bg-card px-6">
      <div className="flex w-10 items-center">
        {onBack ? (
          <div
            onClick={onBack}
            className="flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg bg-muted transition-colors hover:bg-accent"
          >
            <ChevronLeft className="h-4 w-4 stroke-[2.5] text-foreground" />
          </div>
        ) : null}
      </div>

      <h1 className="text-2xl font-serif italic tracking-tight text-foreground">{title}</h1>

      <div className="flex items-center gap-3">
        {showChatToggle && onChatToggle ? (
          <button
            onClick={onChatToggle}
            className={`flex h-10 w-10 items-center justify-center rounded-xl transition-all ${
              isChatOpen ? "bg-primary text-white shadow-lg shadow-primary/30" : "bg-muted text-muted-foreground hover:bg-accent"
            }`}
          >
            <MessageSquare className="h-5 w-5" />
          </button>
        ) : null}

        <div className="h-6 w-px bg-border" />

        <DropdownMenu>
          <DropdownMenuTrigger
            nativeButton={false}
            render={
              <div className="flex h-10 w-10 cursor-pointer items-center justify-center rounded-full bg-primary text-[0.8rem] font-semibold text-white ring-2 ring-border shadow-md transition-opacity hover:opacity-90">
                {userInitials}
              </div>
            }
          />
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuGroup>
              <DropdownMenuLabel>My Account</DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            {onProfile ? (
              <DropdownMenuItem className="cursor-pointer" onClick={onProfile}>
                <User className="mr-2 h-4 w-4" />
                <span>Profile</span>
              </DropdownMenuItem>
            ) : null}
            {onSettings ? (
              <DropdownMenuItem className="cursor-pointer" onClick={onSettings}>
                <Settings className="mr-2 h-4 w-4" />
                <span>Settings</span>
              </DropdownMenuItem>
            ) : null}
            {onPolicy ? (
              <DropdownMenuItem className="cursor-pointer" onClick={onPolicy}>
                <Shield className="mr-2 h-4 w-4" />
                <span>Custom Policy</span>
              </DropdownMenuItem>
            ) : null}
            {onLogout ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem className="cursor-pointer text-destructive focus:text-destructive" onClick={onLogout}>
                  <LogOut className="mr-2 h-4 w-4" />
                  <span>Logout</span>
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
