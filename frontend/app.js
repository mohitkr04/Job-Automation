// AutoApply Application Controller

let sseSource = null;
let profileData = {};

document.addEventListener("DOMContentLoaded", () => {
  initSSE();
  loadApplications();
  loadResumeInfo();
  setupProfileModal();
});

// 1. Live SSE Connection
function initSSE() {
  if (sseSource) {
    sseSource.close();
  }

  sseSource = new EventSource("/api/events");

  sseSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      updatePlatformCards(data);
    } catch (e) {
      console.error("SSE parse error", e);
    }
  };

  sseSource.onerror = (err) => {
    console.warn("SSE connection interrupted, retrying in 3s...", err);
  };
}

// 2. Update UI for Platform Cards
function updatePlatformCards(metrics) {
  const platforms = ["naukri", "linkedin", "indeed", "hirist"];

  platforms.forEach((p) => {
    const data = metrics[p];
    if (!data) return;

    // Connection Badge
    const connEl = document.getElementById(`conn-${p}`);
    if (connEl) {
      connEl.textContent = data.connection_status;
      connEl.className = "connection-badge " + getConnectionClass(data.connection_status);
    }

    // Automation Badge
    const autoEl = document.getElementById(`auto-${p}`);
    if (autoEl) {
      autoEl.textContent = data.automation_status;
      autoEl.className = "automation-badge " + getAutomationClass(data.automation_status);
    }

    // Step Details
    const stepEl = document.getElementById(`step-${p}`);
    if (stepEl) {
      const stepText = data.current_step_details || "Idle";
      const jobText = data.current_job_title ? ` (${data.current_job_title})` : "";
      stepEl.textContent = stepText + jobText;
    }

    // Metrics
    const discEl = document.getElementById(`metric-${p}-discovered`);
    if (discEl) discEl.textContent = data.jobs_discovered || 0;

    const eligEl = document.getElementById(`metric-${p}-eligible`);
    if (eligEl) eligEl.textContent = data.eligible_jobs || 0;

    const appEl = document.getElementById(`metric-${p}-applied`);
    if (appEl) appEl.textContent = data.applications_submitted || 0;

    const failEl = document.getElementById(`metric-${p}-failed`);
    if (failEl) failEl.textContent = data.failed_applications || 0;

    const skipEl = document.getElementById(`metric-${p}-skipped`);
    if (skipEl) skipEl.textContent = data.duplicates_skipped || 0;

    const timeEl = document.getElementById(`metric-${p}-time`);
    if (timeEl) {
      if (data.last_run_time) {
        const d = new Date(data.last_run_time);
        timeEl.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      } else {
        timeEl.textContent = "Never";
      }
    }
  });
}

function getConnectionClass(status) {
  switch (status) {
    case "Connected": return "status-connected";
    case "Authentication Required": return "status-auth-required";
    case "Connecting": return "status-active";
    default: return "status-disconnected";
  }
}

function getAutomationClass(status) {
  switch (status) {
    case "Completed": return "status-completed";
    case "Failed": return "status-failed";
    case "Idle": return "status-idle";
    default: return "status-active";
  }
}

// 3. Platform Action Handlers
async function startPlatform(platformId) {
  showToast(`Starting ${platformId} automation...`);
  try {
    const res = await fetch(`/api/platforms/${platformId}/start`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      showToast(`${platformId} started.`);
      // Refresh applications table after a few moments
      setTimeout(loadApplications, 2000);
    } else {
      showToast(`Error: ${data.detail || "Could not start"}`);
    }
  } catch (e) {
    showToast(`Network error: ${e.message}`);
  }
}

async function stopPlatform(platformId) {
  showToast(`Stopping ${platformId}...`);
  try {
    const res = await fetch(`/api/platforms/${platformId}/stop`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      showToast(`${platformId} stopped.`);
      setTimeout(loadApplications, 1000);
    }
  } catch (e) {
    showToast(`Network error: ${e.message}`);
  }
}

async function testConnection(platformId) {
  showToast(`Testing ${platformId} connection...`);
  try {
    const res = await fetch(`/api/platforms/${platformId}/test-connection`, { method: "POST" });
    const data = await res.json();
    showToast(`${platformId} status: ${data.connection_status}`);
  } catch (e) {
    showToast(`Error testing connection: ${e.message}`);
  }
}

// 4. Applications Ledger & Manual Section
function setQuickFilter(status) {
  document.getElementById("filterStatus").value = status;
  
  // Update active pill state
  document.querySelectorAll(".tab-pill").forEach(el => el.classList.remove("active"));
  if (status === "Manual Action Required") {
    document.getElementById("tabManual").classList.add("active");
  } else if (status === "Applied — Verified") {
    document.getElementById("tabVerified").classList.add("active");
  } else {
    document.getElementById("tabAll").classList.add("active");
  }

  loadApplications();
}

async function triggerManualCleanup() {
  showToast("Running automatic data cleanup (6 days cutoff)...");
  try {
    const res = await fetch("/api/maintenance/cleanup", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      const stats = data.cleanup_stats || {};
      const delManual = stats.deleted_manual_records || 0;
      const purgedSkips = stats.purged_legacy_skips || 0;
      showToast(`Cleaned: ${delManual} outdated manual, ${purgedSkips} residual skips.`);
      await loadApplications();
    } else {
      showToast(`Cleanup error: ${data.detail || "Failed"}`);
    }
  } catch (e) {
    showToast(`Network error: ${e.message}`);
  }
}

async function loadApplications() {
  const platform = document.getElementById("filterPlatform").value;
  const status = document.getElementById("filterStatus").value;

  let url = "/api/applications?limit=200";
  if (platform) url += `&platform=${encodeURIComponent(platform)}`;
  if (status) url += `&status=${encodeURIComponent(status)}`;

  try {
    const res = await fetch(url);
    const data = await res.json();
    renderApplicationsTable(data);
    updateManualBadgeCount(data);
  } catch (e) {
    console.error("Failed to load applications", e);
  }
}

function updateManualBadgeCount(apps) {
  if (!apps) return;
  const manualCount = apps.filter(a => a.status === "Manual Action Required").length;
  const badgeEl = document.getElementById("manualCountBadge");
  if (badgeEl) {
    badgeEl.textContent = manualCount;
  }
}

function renderApplicationsTable(apps) {
  const tbody = document.getElementById("appsTableBody");
  if (!tbody) return;

  if (!apps || apps.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center py-8 text-muted">No records match the active filter.</td></tr>`;
    return;
  }

  tbody.innerHTML = apps.map((app) => {
    const statusClass = getOutcomeClass(app.status);
    const scoreColor = app.match_score >= 80 ? "color: #34d399;" : (app.match_score >= 60 ? "color: #a5b4fc;" : "color: #94a3b8;");
    const dateStr = app.created_at ? new Date(app.created_at).toLocaleDateString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "-";
    const jobLink = app.job_url ? `<a href="${app.job_url}" target="_blank" style="color: #818cf8; text-decoration: none; font-weight: 600;">${escapeHtml(app.job_title)}</a>` : escapeHtml(app.job_title);

    return `
      <tr>
        <td><span class="badge-outcome ${statusClass}">${escapeHtml(app.status)}</span></td>
        <td>
          <div style="font-weight: 600;">${jobLink}</div>
          <div style="font-size: 0.74rem; color: #94a3b8;">${escapeHtml(app.employer_name)}</div>
        </td>
        <td><span style="text-transform: capitalize; font-weight: 600;">${escapeHtml(app.platform)}</span></td>
        <td><span class="score-pill" style="${scoreColor}">${app.match_score}%</span></td>
        <td>
          <div>${escapeHtml(app.job_location || "Open")}</div>
          <div style="font-size: 0.72rem; color: #64748b;">${escapeHtml(app.experience_req || "Not specified")}</div>
        </td>
        <td style="max-width: 260px; font-size: 0.75rem; color: #cbd5e1;">${escapeHtml(app.verification_result || app.failure_reason || "-")}</td>
        <td style="font-family: var(--font-mono); font-size: 0.72rem; color: #64748b;">${dateStr}</td>
      </tr>
    `;
  }).join("");
}

function getOutcomeClass(status) {
  switch (status) {
    case "Applied — Verified": return "badge-verified";
    case "Manual Action Required": return "badge-manual";
    case "Skipped — Duplicate": return "badge-duplicate";
    case "Skipped — Ineligible": return "badge-ineligible";
    default: return "badge-failed";
  }
}

// 5. Data Export
function exportData(format) {
  window.location.href = `/api/applications/export?format=${format}`;
}

// 6. Resume Info
async function loadResumeInfo() {
  try {
    const res = await fetch("/api/resume/info");
    const data = await res.json();
    const pillText = document.getElementById("resumePillText");
    if (pillText && data.exists) {
      pillText.textContent = `Mohit Kumar Resume (${(data.file_size_bytes / 1024).toFixed(1)} KB)`;
    }
  } catch (e) {
    console.error("Resume info error", e);
  }
}

// 7. Profile Modal Management
function setupProfileModal() {
  const modal = document.getElementById("profileModal");
  const btnOpen = document.getElementById("btnOpenProfile");
  const btnClose = document.getElementById("btnCloseProfile");
  const btnCancel = document.getElementById("btnCancelProfile");
  const btnSave = document.getElementById("btnSaveProfile");

  btnOpen.addEventListener("click", async () => {
    modal.style.display = "flex";
    await fetchProfile();
  });

  const closeModal = () => { modal.style.display = "none"; };
  btnClose.addEventListener("click", closeModal);
  btnCancel.addEventListener("click", closeModal);

  btnSave.addEventListener("click", async (e) => {
    e.preventDefault();
    await saveProfile();
    closeModal();
  });
}

async function fetchProfile() {
  try {
    const res = await fetch("/api/profile");
    profileData = await res.json();

    const p = profileData.personal || {};
    const e = profileData.education || {};
    const pref = profileData.preferences || {};

    document.getElementById("profFullName").value = p.full_name || "";
    document.getElementById("profEmail").value = p.email || "";
    document.getElementById("profPhone").value = p.phone || "";
    document.getElementById("profLocation").value = p.current_location || "";
    document.getElementById("profLinkedIn").value = p.linkedin_url || "";
    document.getElementById("profGitHub").value = p.github_url || "";

    document.getElementById("profClass10").value = e.class_10_percentage || "";
    document.getElementById("profClass12").value = e.class_12_percentage || "";
    document.getElementById("profCollegeMarks").value = e.college_percentage || "";
    document.getElementById("profDegree").value = `${e.degree || ""} in ${e.specialization || ""}`.trim();
    document.getElementById("profCollege").value = e.college_name || "";

    document.getElementById("profNotice").value = pref.notice_period_text || "15 days";
    document.getElementById("profCTC").value = pref.expected_ctc_lpa || "9-10 LPA";
    document.getElementById("profLocations").value = (pref.preferred_locations || []).join(", ");
    document.getElementById("profRoles").value = (profileData.target_roles || []).join(", ");
    document.getElementById("profSkills").value = (profileData.skills || []).join(", ");
  } catch (err) {
    showToast("Failed to fetch profile");
  }
}

async function saveProfile() {
  try {
    if (!profileData.personal) profileData.personal = {};
    if (!profileData.education) profileData.education = {};
    if (!profileData.preferences) profileData.preferences = {};

    profileData.personal.full_name = document.getElementById("profFullName").value;
    profileData.personal.email = document.getElementById("profEmail").value;
    profileData.personal.phone = document.getElementById("profPhone").value;
    profileData.personal.current_location = document.getElementById("profLocation").value;
    profileData.personal.linkedin_url = document.getElementById("profLinkedIn").value;
    profileData.personal.github_url = document.getElementById("profGitHub").value;

    profileData.education.class_10_percentage = parseFloat(document.getElementById("profClass10").value) || 0;
    profileData.education.class_12_percentage = parseFloat(document.getElementById("profClass12").value) || 0;
    profileData.education.college_percentage = parseFloat(document.getElementById("profCollegeMarks").value) || 0;

    profileData.preferences.notice_period_text = document.getElementById("profNotice").value;
    profileData.preferences.expected_ctc_lpa = document.getElementById("profCTC").value;
    profileData.preferences.preferred_locations = document.getElementById("profLocations").value.split(",").map(s => s.trim()).filter(Boolean);
    profileData.target_roles = document.getElementById("profRoles").value.split(",").map(s => s.trim()).filter(Boolean);
    profileData.skills = document.getElementById("profSkills").value.split(",").map(s => s.trim()).filter(Boolean);

    const res = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profileData)
    });

    if (res.ok) {
      showToast("Profile saved successfully.");
    } else {
      showToast("Failed to save profile.");
    }
  } catch (e) {
    showToast("Error updating profile.");
  }
}

// 8. Toast Helper
function showToast(msg) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => {
    toast.classList.remove("show");
  }, 3500);
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
