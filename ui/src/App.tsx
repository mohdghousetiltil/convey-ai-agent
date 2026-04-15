import React, { useState } from "react";
import { UploadScreen } from "./components/UploadScreen";
import { LoadingScreen } from "./components/LoadingScreen";
import { ReviewScreen } from "./components/ReviewScreen";
import { SettingsScreen } from "./components/SettingsScreen";
import { ProfileScreen } from "./components/ProfileScreen";
import { ClientPolicyScreen } from "./components/ClientPolicyScreen";
import {
  AnswerUpdatePayload,
  ReviewRunPayload,
  autofillTriConvey,
  createRun,
  saveAnswers,
} from "./lib/api";

type ViewState = "upload" | "loading" | "main" | "settings" | "profile" | "policy";

export default function App() {
  const [view, setView] = useState<ViewState>("upload");
  const [run, setRun] = useState<ReviewRunPayload | null>(null);
  const [loadingMessage, setLoadingMessage] = useState("Extracting Section 32 answers, review flags, and autofill actions...");
  const [uploadError, setUploadError] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [autofilling, setAutofilling] = useState(false);

  const handleUploadComplete = async (files: File[]) => {
    setUploadError("");
    setLoadingMessage("Extracting Section 32 answers, review flags, and autofill actions...");
    setView("loading");
    try {
      const nextRun = await createRun(files);
      setRun(nextRun);
      setView("main");
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Failed to analyze the uploaded PDFs.");
      setView("upload");
    }
  };

  const handleSaveReview = async (updates: Record<string, AnswerUpdatePayload>) => {
    if (!run || saving) {
      return;
    }
    setSaving(true);
    try {
      const nextRun = await saveAnswers(run.manifest.run_id, updates);
      setRun(nextRun);
    } finally {
      setSaving(false);
    }
  };

  const handleAutofill = async (updates: Record<string, AnswerUpdatePayload>) => {
    if (!run || autofilling) {
      return;
    }
    setAutofilling(true);
    try {
      const savedRun = Object.keys(updates).length ? await saveAnswers(run.manifest.run_id, updates) : run;
      setRun(savedRun);
      setLoadingMessage("Starting TriConvey autofill and waiting for execution feedback...");
      setView("loading");
      const completedRun = await autofillTriConvey(savedRun.manifest.run_id, { skipReviewGate: true });
      setRun(completedRun);
      setView("main");
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Autofill failed.");
      setView("main");
    } finally {
      setAutofilling(false);
    }
  };

  const handleLogout = () => {
    setRun(null);
    setUploadError("");
    setView("upload");
  };

  switch (view) {
    case "upload":
      return (
        <UploadScreen
          onUploadComplete={handleUploadComplete}
          errorMessage={uploadError}
        />
      );
    case "loading":
      return <LoadingScreen message={loadingMessage} />;
    case "main":
      if (!run) {
        return (
          <UploadScreen
            onUploadComplete={handleUploadComplete}
            errorMessage="No review run is loaded yet."
          />
        );
      }
      return (
        <ReviewScreen
          run={run}
          onBack={() => setView("upload")}
          onProfile={() => setView("profile")}
          onSettings={() => setView("settings")}
          onPolicy={() => setView("policy")}
          onLogout={handleLogout}
          onSaveReview={handleSaveReview}
          onAutofill={handleAutofill}
          isSaving={saving}
          isAutofilling={autofilling}
          errorMessage={uploadError}
          onDismissError={() => setUploadError("")}
        />
      );
    case "settings":
      return <SettingsScreen onBack={() => setView("main")} />;
    case "profile":
      return <ProfileScreen onBack={() => setView("main")} />;
    case "policy":
      return <ClientPolicyScreen onBack={() => setView("main")} />;
    default:
      return (
        <UploadScreen
          onUploadComplete={handleUploadComplete}
          errorMessage={uploadError}
        />
      );
  }
}
