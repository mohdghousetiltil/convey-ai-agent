import React from "react";
import { motion } from "motion/react";
import { Mail, Shield, Building2, Calendar } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { useAuth } from "../lib/AuthContext";
import { Header } from "./Header";

interface ProfileScreenProps {
  onBack: () => void;
  onSettings?: () => void;
  onPolicy?: () => void;
  onAbout?: () => void;
  onLogout?: () => void;
}

export function ProfileScreen({ onBack, onSettings, onPolicy, onAbout, onLogout }: ProfileScreenProps) {
  const { user } = useAuth();

  const displayName = user?.name || "Unknown User";
  const email = user?.email || "—";
  const role = user?.role ? user.role.charAt(0).toUpperCase() + user.role.slice(1) : "—";
  const clientName = user?.client_name || "—";
  const initials = displayName
    .split(" ")
    .map((n) => n[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="min-h-screen bg-background font-sans">
      <Header
        onBack={onBack}
        userInitials={initials}
        onSettings={onSettings}
        onPolicy={onPolicy}
        onLogout={onLogout}
      />

      <main className="max-w-3xl mx-auto p-8 space-y-8">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="space-y-8"
        >
          <div className="flex flex-col items-center text-center space-y-4">
            <Avatar className="w-24 h-24 border-4 border-border shadow-xl">
              <AvatarFallback className="bg-primary text-white text-2xl font-bold">
                {initials}
              </AvatarFallback>
            </Avatar>
            <div>
              <h2 className="text-2xl font-bold text-foreground">{displayName}</h2>
              <p className="text-muted-foreground font-medium">{role}</p>
            </div>
          </div>

          <Card className="border-border shadow-sm">
            <CardContent className="p-6 space-y-6">
              <div className="grid md:grid-cols-2 gap-6">
                <div className="space-y-1">
                  <p className="text-xs font-bold text-muted-foreground uppercase tracking-wider">Email Address</p>
                  <div className="flex items-center gap-2 text-foreground">
                    <Mail className="w-4 h-4 text-muted-foreground shrink-0" />
                    <span className="text-sm font-semibold break-all">{email}</span>
                  </div>
                </div>
                <div className="space-y-1">
                  <p className="text-xs font-bold text-muted-foreground uppercase tracking-wider">Role</p>
                  <div className="flex items-center gap-2 text-foreground">
                    <Shield className="w-4 h-4 text-muted-foreground shrink-0" />
                    <span className="text-sm font-semibold">{role}</span>
                  </div>
                </div>
                <div className="space-y-1">
                  <p className="text-xs font-bold text-muted-foreground uppercase tracking-wider">Organisation</p>
                  <div className="flex items-center gap-2 text-foreground">
                    <Building2 className="w-4 h-4 text-muted-foreground shrink-0" />
                    <span className="text-sm font-semibold">{clientName}</span>
                  </div>
                </div>
                {user?.client_slug ? (
                  <div className="space-y-1">
                    <p className="text-xs font-bold text-muted-foreground uppercase tracking-wider">Workspace</p>
                    <div className="flex items-center gap-2 text-foreground">
                      <Calendar className="w-4 h-4 text-muted-foreground shrink-0" />
                      <span className="text-sm font-semibold text-muted-foreground">{user.client_slug}</span>
                    </div>
                  </div>
                ) : null}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </main>
    </div>
  );
}
