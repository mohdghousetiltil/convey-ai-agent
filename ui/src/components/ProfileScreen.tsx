import React from "react";
import { motion } from "motion/react";
import { ChevronLeft, Mail, Shield, Building2, Calendar } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { useAuth } from "../lib/AuthContext";

interface ProfileScreenProps {
  onBack: () => void;
}

export function ProfileScreen({ onBack }: ProfileScreenProps) {
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
    <div className="min-h-screen bg-slate-50 font-sans">
      <header className="h-16 border-b bg-white flex items-center px-6 sticky top-0 z-50">
        <div
          onClick={onBack}
          className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center cursor-pointer hover:bg-slate-200 transition-colors mr-4"
        >
          <ChevronLeft className="w-4 h-4 text-foreground stroke-[2.5]" />
        </div>
        <h1 className="text-lg font-bold">Profile</h1>
      </header>

      <main className="max-w-3xl mx-auto p-8 space-y-8">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="space-y-8"
        >
          <div className="flex flex-col items-center text-center space-y-4">
            <Avatar className="w-24 h-24 border-4 border-white shadow-xl">
              <AvatarFallback className="bg-primary text-white text-2xl font-bold">
                {initials}
              </AvatarFallback>
            </Avatar>
            <div>
              <h2 className="text-2xl font-bold text-slate-900">{displayName}</h2>
              <p className="text-slate-500 font-medium">{role}</p>
            </div>
          </div>

          <Card className="border-slate-200 shadow-sm">
            <CardContent className="p-6 space-y-6">
              <div className="grid md:grid-cols-2 gap-6">
                <div className="space-y-1">
                  <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Email Address</p>
                  <div className="flex items-center gap-2 text-slate-700">
                    <Mail className="w-4 h-4 text-slate-400 shrink-0" />
                    <span className="text-sm font-semibold break-all">{email}</span>
                  </div>
                </div>
                <div className="space-y-1">
                  <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Role</p>
                  <div className="flex items-center gap-2 text-slate-700">
                    <Shield className="w-4 h-4 text-slate-400 shrink-0" />
                    <span className="text-sm font-semibold">{role}</span>
                  </div>
                </div>
                <div className="space-y-1">
                  <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Organisation</p>
                  <div className="flex items-center gap-2 text-slate-700">
                    <Building2 className="w-4 h-4 text-slate-400 shrink-0" />
                    <span className="text-sm font-semibold">{clientName}</span>
                  </div>
                </div>
                {user?.client_slug ? (
                  <div className="space-y-1">
                    <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Workspace</p>
                    <div className="flex items-center gap-2 text-slate-700">
                      <Calendar className="w-4 h-4 text-slate-400 shrink-0" />
                      <span className="text-sm font-semibold text-slate-500">{user.client_slug}</span>
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
