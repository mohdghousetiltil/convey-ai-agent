import React from "react";
import { motion } from "motion/react";
import { ChevronLeft, User as UserIcon, Mail, Phone, MapPin, Calendar, Edit3 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Card, CardContent } from "@/components/ui/card";

interface ProfileScreenProps {
  onBack: () => void;
}

export function ProfileScreen({ onBack }: ProfileScreenProps) {
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
            <div className="relative">
              <Avatar className="w-24 h-24 border-4 border-white shadow-xl">
                <AvatarFallback className="bg-primary text-white text-2xl font-bold">JD</AvatarFallback>
              </Avatar>
              <button className="absolute bottom-0 right-0 p-2 bg-white rounded-full shadow-lg border border-slate-100 text-primary hover:bg-slate-50 transition-colors">
                <Edit3 className="w-4 h-4" />
              </button>
            </div>
            <div>
              <h2 className="text-2xl font-bold text-slate-900">John Doe</h2>
              <p className="text-slate-500 font-medium">TriConvey Automation Lead</p>
            </div>
          </div>

          <div className="grid gap-4">
            <Card className="border-slate-200 shadow-sm">
              <CardContent className="p-6 space-y-6">
                <div className="grid md:grid-cols-2 gap-6">
                  <div className="space-y-1">
                    <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Email Address</p>
                    <div className="flex items-center gap-2 text-slate-700">
                      <Mail className="w-4 h-4 text-slate-400" />
                      <span className="text-sm font-semibold">john.doe@conveyfirm.com</span>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Phone Number</p>
                    <div className="flex items-center gap-2 text-slate-700">
                      <Phone className="w-4 h-4 text-slate-400" />
                      <span className="text-sm font-semibold">+61 400 000 000</span>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Office Location</p>
                    <div className="flex items-center gap-2 text-slate-700">
                      <MapPin className="w-4 h-4 text-slate-400" />
                      <span className="text-sm font-semibold">Melbourne, VIC</span>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Member Since</p>
                    <div className="flex items-center gap-2 text-slate-700">
                      <Calendar className="w-4 h-4 text-slate-400" />
                      <span className="text-sm font-semibold">January 2024</span>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="pt-4">
            <Button variant="outline" className="w-full border-slate-200 text-slate-600 font-bold h-12 rounded-xl">
              Edit Public Profile
            </Button>
          </div>
        </motion.div>
      </main>
    </div>
  );
}
