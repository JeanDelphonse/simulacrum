import { useState } from "react";
import { ChevronDown, Zap, Play, Clock, Check, Mail, FileText, Users, Calendar, BarChart3, Target, MessageCircle, AlertTriangle, ArrowRight, DollarSign, TrendingUp, X, Eye, MousePointer, Reply, Ban, Send, Briefcase, Globe, Search } from "lucide-react";

const CYCLES = [
  { id: 9, phase: "exploit", date: "2026-06-03", time: "2:14 PM", agents: 3, escalated: 0, cost: "$0.42", contacts: 10, emails: 10, replies: 3,
    insight: "Consulting outreach scored highest (0.72) — reply rate increased 3% since last cycle. Speaking proposals dispatched for the first time based on LinkedIn engagement. Cold email deprioritized after bounce rate increased.",
    dispatched: [
      { agent: "Consulting Outreach", layer: "L1", status: "complete", cost: "$0.18", tokens: "12,400 in / 3,200 out", artifact: "art_xR4" },
      { agent: "Rate Card", layer: "L1", status: "complete", cost: "$0.12", tokens: "8,100 in / 2,800 out", artifact: "art_yK7", note: "Re-run with updated positioning" },
      { agent: "Speaking Proposals", layer: "L2", status: "complete", cost: "$0.12", tokens: "9,600 in / 3,100 out", artifact: "art_gA7", note: "First dispatch" },
    ],
    emails_detail: [
      { to: "Sarah Chen", company: "Acme AI", subject: "Quick question about your ML infrastructure", status: "replied", sent: "2:16 PM", opened: "3:42 PM", replied: "4:18 PM" },
      { to: "Marcus Webb", company: "DataFlow Inc", subject: "Streamlining your data pipeline team", status: "opened", sent: "2:16 PM", opened: "5:01 PM", replied: null },
      { to: "Priya Nambiar", company: "NeuralScale", subject: "AI program assessment for NeuralScale", status: "replied", sent: "2:17 PM", opened: "2:45 PM", replied: "3:30 PM" },
      { to: "James Liu", company: "Vertex ML", subject: "Scaling your AI team efficiently", status: "opened", sent: "2:17 PM", opened: "6:22 PM", replied: null },
      { to: "Anika Patel", company: "SynthAI Labs", subject: "Process optimization for SynthAI", status: "replied", sent: "2:18 PM", opened: "2:30 PM", replied: "2:55 PM" },
      { to: "David Kim", company: "CloudBridge", subject: "Your vendor management challenges", status: "sent", sent: "2:18 PM", opened: null, replied: null },
      { to: "Rachel Torres", company: "AutoML Co", subject: "Fractional AI TPM for AutoML", status: "sent", sent: "2:19 PM", opened: null, replied: null },
      { to: "Kevin Okafor", company: "DataPipe", subject: "Program assessment for DataPipe", status: "bounced", sent: "2:19 PM", opened: null, replied: null },
      { to: "Lisa Chang", company: "MLOps Inc", subject: "AI infrastructure consulting", status: "sent", sent: "2:20 PM", opened: null, replied: null },
      { to: "Thomas Berg", company: "ScaleAI Pro", subject: "Building your AI program", status: "opened", sent: "2:20 PM", opened: "7:15 PM", replied: null },
    ],
    contacts_added: [
      { name: "Sarah Chen", company: "Acme AI", source: "consulting_outreach", stage: "active" },
      { name: "Marcus Webb", company: "DataFlow Inc", source: "consulting_outreach", stage: "prospect" },
      { name: "Priya Nambiar", company: "NeuralScale", source: "consulting_outreach", stage: "active" },
      { name: "Anika Patel", company: "SynthAI Labs", source: "consulting_outreach", stage: "active" },
    ],
    steps_executed: [
      { agent: "Cold Email (Batch 2)", step: "Follow-up 1", contact: "Robert Hayes", status: "executed", detail: "Sent follow-up — no reply after 72h", urgency: null, suggestedDate: null },
      { agent: "Cold Email (Batch 2)", step: "Follow-up 1", contact: "Emily Frost", status: "skipped", detail: "Prospect replied on Jun 1", urgency: null, suggestedDate: null },
      { agent: "Cold Email (Batch 2)", step: "Follow-up 1", contact: "Mark Stevens", status: "executed", detail: "Sent follow-up — no reply after 72h", urgency: null, suggestedDate: null },
      { agent: "Cold Email (Batch 2)", step: "Breakup Email", contact: "Robert Hayes", status: "scheduled", detail: "Final follow-up if no reply", urgency: "high", suggestedDate: "Jun 7", daysLeft: 2 },
      { agent: "Cold Email (Batch 2)", step: "Breakup Email", contact: "Mark Stevens", status: "scheduled", detail: "Final follow-up if no reply", urgency: "high", suggestedDate: "Jun 7", daysLeft: 2 },
      { agent: "Consulting Outreach", step: "Follow-up 1", contact: "Marcus Webb", status: "scheduled", detail: "Check-in if no reply to initial email", urgency: "medium", suggestedDate: "Jun 6", daysLeft: 3 },
      { agent: "Consulting Outreach", step: "Follow-up 1", contact: "David Kim", status: "scheduled", detail: "Check-in if no reply to initial email", urgency: "medium", suggestedDate: "Jun 6", daysLeft: 3 },
      { agent: "Consulting Outreach", step: "Follow-up 1", contact: "Lisa Chang", status: "scheduled", detail: "Check-in if no reply to initial email", urgency: "low", suggestedDate: "Jun 8", daysLeft: 5 },
      { agent: "Consulting Outreach", step: "Final Check-in", contact: "James Liu", status: "scheduled", detail: "Last touchpoint before closing loop", urgency: "medium", suggestedDate: "Jun 8", daysLeft: 5 },
      { agent: "Consulting Proposal", step: "Proposal Follow-up", contact: "Anika Patel", status: "scheduled", detail: "Nudge if proposal not signed", urgency: "low", suggestedDate: "Jun 10", daysLeft: 7 },
    ],
    bookings: [
      { name: "Anika Patel", company: "SynthAI Labs", type: "Discovery Call", date: "Jun 5, 2:00 PM" },
      { name: "Priya Nambiar", company: "NeuralScale", type: "Advisory Call", date: "Jun 6, 10:00 AM" },
    ],
  },
  { id: 8, phase: "exploit", date: "2026-06-02", time: "2:12 PM", agents: 2, escalated: 1, cost: "$0.38", contacts: 25, emails: 25, replies: 2,
    insight: "Cold email and consulting outreach dispatched. Cold email scored 0.62 (moderate). Consulting outreach scored 0.65 (rising). Speaking proposals not yet above threshold.",
    dispatched: [
      { agent: "Cold Email Campaign", layer: "L1", status: "complete", cost: "$0.22", tokens: "14,200 in / 4,800 out", artifact: "art_zN5" },
      { agent: "Consulting Outreach", layer: "L1", status: "complete", cost: "$0.16", tokens: "11,400 in / 3,600 out", artifact: "art_aM3" },
    ],
    emails_detail: [],
    contacts_added: [],
    steps_executed: [],
    bookings: [],
  },
  { id: 7, phase: "explore", date: "2026-06-01", time: "2:08 PM", agents: 5, escalated: 0, cost: "$0.65", contacts: 0, emails: 0, replies: 0,
    insight: "Explore phase — diversifying agent selection. Rate card, LinkedIn, booking page, proposal, and referral network dispatched. Building foundational artifacts.",
    dispatched: [
      { agent: "Rate Card", layer: "L1", status: "complete", cost: "$0.12", tokens: "8,100 in / 2,800 out", artifact: "art_yK7" },
      { agent: "LinkedIn Profile", layer: "L1", status: "complete", cost: "$0.10", tokens: "6,400 in / 2,200 out", artifact: "art_cW4" },
      { agent: "Booking Page", layer: "L1", status: "complete", cost: "$0.08", tokens: "5,200 in / 1,800 out", artifact: "art_dE6" },
      { agent: "Consulting Proposal", layer: "L1", status: "complete", cost: "$0.18", tokens: "12,000 in / 4,200 out", artifact: "art_bQ9" },
      { agent: "Referral Network", layer: "L1", status: "complete", cost: "$0.17", tokens: "11,800 in / 3,900 out", artifact: "art_xR4" },
    ],
    emails_detail: [],
    contacts_added: [],
    steps_executed: [],
    bookings: [],
  },
];

const EMAIL_STATUS = {
  replied: { label: "Replied", color: "#1A8754", bg: "#E6F7EF", Icon: Reply },
  opened: { label: "Opened", color: "#534AB7", bg: "#EEEDFE", Icon: Eye },
  sent: { label: "Sent", color: "#6b7b8d", bg: "#F0F1F3", Icon: Send },
  bounced: { label: "Bounced", color: "#E85D4A", bg: "#FDECEA", Icon: Ban },
};

export default function CycleProgress() {
  const [expanded, setExpanded] = useState(9);
  const [expandedSections, setExpandedSections] = useState({ agents: true, emails: true, contacts: false, steps: false, bookings: false });

  const toggleSection = (key) => setExpandedSections(p => ({ ...p, [key]: !p[key] }));

  const SectionHeader = ({ id, icon: Icon, title, count, color }) => (
    <div onClick={() => toggleSection(id)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 0", cursor: "pointer", borderBottom: expandedSections[id] ? "1px solid rgba(0,0,0,.04)" : "none" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Icon size={15} color={color || "#0F7B72"} />
        <span style={{ fontSize: 13, fontWeight: 600, color: "#0D1B3E" }}>{title}</span>
        {count !== undefined && <span style={{ fontSize: 11, fontWeight: 500, color: "#0F7B72", background: "#D6F0EE", padding: "1px 8px", borderRadius: 100 }}>{count}</span>}
      </div>
      <ChevronDown size={14} color="#9CA3AF" style={{ transform: expandedSections[id] ? "rotate(180deg)" : "none", transition: "transform .2s" }} />
    </div>
  );

  return (
    <div style={{ fontFamily: "'DM Sans', sans-serif", background: "#FAFBFC", minHeight: "100vh" }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=Playfair+Display:wght@600;700&display=swap');*{box-sizing:border-box;margin:0;padding:0}.cycle-row{transition:background .15s;cursor:pointer}.cycle-row:hover{background:rgba(0,0,0,.01)}.email-row{transition:background .1s}.email-row:hover{background:rgba(0,0,0,.015)}`}</style>

      <nav style={{ background: "white", borderBottom: "1px solid rgba(0,0,0,.06)", padding: "0 24px", height: 64, display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 32, height: 32, borderRadius: 8, background: "linear-gradient(135deg,#0d1b3e,#0f7b72)", display: "flex", alignItems: "center", justifyContent: "center" }}><Zap size={16} color="white" /></div>
        <span style={{ fontFamily: "'Playfair Display'", fontWeight: 700, fontSize: 20, color: "#0D1B3E" }}>Simulacrum</span>
      </nav>

      <div style={{ background: "white", borderBottom: "1px solid rgba(0,0,0,.06)", padding: "0 24px", display: "flex", gap: 4 }}>
        {["Journey","Action Queue","Income","Momentum","Cycle","Visuals","Agent Network","Escalations"].map((t, i) => (
          <button key={t} style={{ background: "none", border: "none", padding: "12px 14px", cursor: "pointer", fontSize: 13, fontWeight: i === 4 ? 600 : 400, color: i === 4 ? "#0F7B72" : "#6b7b8d", borderBottom: i === 4 ? "2px solid #0F7B72" : "2px solid transparent" }}>{t}</button>
        ))}
      </div>

      <div style={{ maxWidth: 960, margin: "0 auto", padding: "24px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <h2 style={{ fontFamily: "'Playfair Display'", fontSize: 22, fontWeight: 700, color: "#0D1B3E" }}>Cycle History</h2>
          <button style={{ display: "flex", alignItems: "center", gap: 6, background: "#0F7B72", border: "none", borderRadius: 8, padding: "8px 16px", fontSize: 13, fontWeight: 600, color: "white", cursor: "pointer" }}><Play size={14} /> Run Cycle</button>
        </div>

        {CYCLES.map(cycle => {
          const isExp = expanded === cycle.id;
          return (
            <div key={cycle.id} style={{ marginBottom: 10, borderRadius: 12, overflow: "hidden", border: "1px solid rgba(0,0,0,.06)", background: "white" }}>
              {/* Cycle header */}
              <div className="cycle-row" onClick={() => setExpanded(isExp ? null : cycle.id)}
                style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 20px", background: isExp ? "#FAFBFC" : "white" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                  <div style={{ width: 38, height: 38, borderRadius: 10, background: cycle.phase === "exploit" ? "#0F7B7212" : "#534AB712", display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <span style={{ fontFamily: "'Playfair Display'", fontSize: 16, fontWeight: 700, color: cycle.phase === "exploit" ? "#0F7B72" : "#534AB7" }}>{cycle.id}</span>
                  </div>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 15, fontWeight: 600, color: "#0D1B3E" }}>Cycle {cycle.id}</span>
                      <span style={{ fontSize: 10, fontWeight: 600, color: cycle.phase === "exploit" ? "#0F7B72" : "#534AB7", background: cycle.phase === "exploit" ? "#D6F0EE" : "#EEEDFE", padding: "2px 8px", borderRadius: 100, textTransform: "uppercase" }}>{cycle.phase}</span>
                    </div>
                    <span style={{ fontSize: 12, color: "#6b7b8d" }}>{cycle.date} · {cycle.time}</span>
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 12 }}>
                  <span style={{ color: "#0D1B3E" }}><strong>{cycle.agents}</strong> agents</span>
                  {cycle.emails > 0 && <span style={{ color: "#534AB7" }}><strong>{cycle.emails}</strong> emails</span>}
                  {cycle.replies > 0 && <span style={{ color: "#1A8754" }}><strong>{cycle.replies}</strong> replies</span>}
                  {cycle.contacts > 0 && <span style={{ color: "#0F7B72" }}><strong>{cycle.contacts}</strong> contacts</span>}
                  <span style={{ color: "#6b7b8d" }}>{cycle.cost}</span>
                  <ChevronDown size={16} color="#9CA3AF" style={{ transform: isExp ? "rotate(180deg)" : "none", transition: "transform .2s" }} />
                </div>
              </div>

              {/* Expanded detail */}
              {isExp && (
                <div style={{ padding: "0 20px 20px" }}>
                  {/* Insight */}
                  <div style={{ padding: "14px 16px", background: "#F4F8FB", borderRadius: 10, marginBottom: 16, borderLeft: "3px solid #0F7B72" }}>
                    <div style={{ fontSize: 10, fontWeight: 600, color: "#0F7B72", textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>Orchestrator reasoning</div>
                    <p style={{ fontSize: 13, color: "#4a5568", lineHeight: 1.7 }}>{cycle.insight}</p>
                  </div>

                  {/* AGENTS */}
                  <SectionHeader id="agents" icon={Target} title="Agents Dispatched" count={cycle.dispatched.length} />
                  {expandedSections.agents && (
                    <div style={{ marginBottom: 16 }}>
                      {cycle.dispatched.map((a, i) => (
                        <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 12px", borderBottom: i < cycle.dispatched.length - 1 ? "1px solid rgba(0,0,0,.03)" : "none" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                            <div style={{ width: 18, height: 18, borderRadius: "50%", background: "#1A8754", display: "flex", alignItems: "center", justifyContent: "center" }}><Check size={10} color="white" strokeWidth={3} /></div>
                            <div>
                              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                <span style={{ fontSize: 13, fontWeight: 500, color: "#0D1B3E" }}>{a.agent}</span>
                                <span style={{ fontSize: 10, color: "#6b7b8d" }}>{a.layer}</span>
                                {a.note && <span style={{ fontSize: 9, color: "#C9952A", background: "#FDF3DC", padding: "1px 6px", borderRadius: 4 }}>{a.note}</span>}
                              </div>
                              <span style={{ fontSize: 11, color: "#9CA3AF" }}>{a.tokens} · {a.cost}</span>
                            </div>
                          </div>
                          <a href="#" style={{ fontSize: 12, color: "#0F7B72", textDecoration: "none", fontWeight: 500, display: "flex", alignItems: "center", gap: 4 }}>View artifact <ArrowRight size={11} /></a>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* EMAILS */}
                  {cycle.emails_detail.length > 0 && (<>
                    <SectionHeader id="emails" icon={Mail} title="Emails Sent" count={cycle.emails_detail.length} color="#534AB7" />
                    {expandedSections.emails && (
                      <div style={{ marginBottom: 16 }}>
                        {/* Summary bar */}
                        <div style={{ display: "flex", gap: 12, padding: "8px 12px", marginBottom: 8 }}>
                          {[
                            { label: "Replied", count: cycle.emails_detail.filter(e => e.status === "replied").length, color: "#1A8754" },
                            { label: "Opened", count: cycle.emails_detail.filter(e => e.status === "opened").length, color: "#534AB7" },
                            { label: "Sent", count: cycle.emails_detail.filter(e => e.status === "sent").length, color: "#6b7b8d" },
                            { label: "Bounced", count: cycle.emails_detail.filter(e => e.status === "bounced").length, color: "#E85D4A" },
                          ].filter(s => s.count > 0).map((s, i) => (
                            <span key={i} style={{ fontSize: 11, fontWeight: 500, color: s.color }}>{s.count} {s.label.toLowerCase()}</span>
                          ))}
                        </div>
                        {/* Email rows */}
                        {cycle.emails_detail.map((e, i) => {
                          const st = EMAIL_STATUS[e.status];
                          return (
                            <div key={i} className="email-row" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 12px", borderBottom: i < cycle.emails_detail.length - 1 ? "1px solid rgba(0,0,0,.03)" : "none" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: 10, flex: 1, minWidth: 0 }}>
                                <st.Icon size={13} color={st.color} />
                                <div style={{ minWidth: 0 }}>
                                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                    <span style={{ fontSize: 13, fontWeight: 500, color: "#0D1B3E" }}>{e.to}</span>
                                    <span style={{ fontSize: 11, color: "#9CA3AF" }}>· {e.company}</span>
                                  </div>
                                  <div style={{ fontSize: 11, color: "#9CA3AF", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.subject}</div>
                                </div>
                              </div>
                              <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
                                <span style={{ fontSize: 10, fontWeight: 500, color: st.color, background: st.bg, padding: "2px 8px", borderRadius: 100 }}>{st.label}</span>
                                <span style={{ fontSize: 10, color: "#b0b8c4", minWidth: 55 }}>{e.sent}</span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </>)}

                  {/* CONTACTS ADDED */}
                  {cycle.contacts_added.length > 0 && (<>
                    <SectionHeader id="contacts" icon={Users} title="Contacts Added to CRM" count={cycle.contacts_added.length} color="#0F7B72" />
                    {expandedSections.contacts && (
                      <div style={{ marginBottom: 16 }}>
                        {cycle.contacts_added.map((c, i) => (
                          <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 12px", borderBottom: i < cycle.contacts_added.length - 1 ? "1px solid rgba(0,0,0,.03)" : "none" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <div style={{ width: 28, height: 28, borderRadius: "50%", background: "#0F7B7210", display: "flex", alignItems: "center", justifyContent: "center" }}>
                                <span style={{ fontSize: 10, fontWeight: 600, color: "#0F7B72" }}>{c.name.split(" ").map(n => n[0]).join("")}</span>
                              </div>
                              <div>
                                <span style={{ fontSize: 13, fontWeight: 500, color: "#0D1B3E" }}>{c.name}</span>
                                <span style={{ fontSize: 11, color: "#9CA3AF" }}> · {c.company}</span>
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: 8 }}>
                              <span style={{ fontSize: 10, color: "#6b7b8d", background: "#F0F1F3", padding: "2px 8px", borderRadius: 4 }}>{c.source.replace(/_/g, " ")}</span>
                              <span style={{ fontSize: 10, fontWeight: 500, color: c.stage === "active" ? "#1A8754" : "#6b7b8d", background: c.stage === "active" ? "#E6F7EF" : "#F0F1F3", padding: "2px 8px", borderRadius: 4 }}>{c.stage}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </>)}

                  {/* ACTION STEPS */}
                  {cycle.steps_executed.length > 0 && (<>
                    <SectionHeader id="steps" icon={Clock} title="Action Steps" count={`${cycle.steps_executed.filter(s=>s.status!=="scheduled").length} processed · ${cycle.steps_executed.filter(s=>s.status==="scheduled").length} upcoming`} color="#C9952A" />
                    {expandedSections.steps && (
                      <div style={{ marginBottom: 16 }}>
                        {/* Completed/skipped steps */}
                        {cycle.steps_executed.filter(s => s.status !== "scheduled").map((s, i) => (
                          <div key={`done-${i}`} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 12px", background: s.status === "skipped" ? "#FAFBFC" : "white", borderBottom: "1px solid rgba(0,0,0,.03)" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              {s.status === "executed" ? <Check size={13} color="#1A8754" /> : <X size={13} color="#9CA3AF" />}
                              <div>
                                <span style={{ fontSize: 13, color: "#0D1B3E" }}>{s.agent} → {s.step}</span>
                                <span style={{ fontSize: 11, color: "#9CA3AF" }}> · {s.contact}</span>
                              </div>
                            </div>
                            <span style={{ fontSize: 10, color: s.status === "executed" ? "#1A8754" : "#9CA3AF", fontWeight: 500 }}>{s.detail}</span>
                          </div>
                        ))}
                        {/* Upcoming scheduled steps with urgency */}
                        {cycle.steps_executed.filter(s => s.status === "scheduled").length > 0 && (
                          <div style={{ padding: "10px 12px 4px", marginTop: 4 }}>
                            <div style={{ fontSize: 10, fontWeight: 600, color: "#C9952A", textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>Upcoming — Scheduled Steps</div>
                          </div>
                        )}
                        {cycle.steps_executed.filter(s => s.status === "scheduled").map((s, i) => {
                          const uColor = s.urgency === "high" ? "#E85D4A" : s.urgency === "medium" ? "#C9952A" : "#6b7b8d";
                          const uBg = s.urgency === "high" ? "#FDECEA" : s.urgency === "medium" ? "#FDF3DC" : "#F0F1F3";
                          const uLabel = s.urgency === "high" ? "Urgent" : s.urgency === "medium" ? "Soon" : "Upcoming";
                          return (
                            <div key={`sched-${i}`} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 12px", borderBottom: "1px solid rgba(0,0,0,.03)", borderLeft: `3px solid ${uColor}`, marginBottom: 2, borderRadius: 4, background: `${uColor}04` }}>
                              <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0 }}>
                                <Clock size={13} color={uColor} />
                                <div style={{ minWidth: 0 }}>
                                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                                    <span style={{ fontSize: 13, fontWeight: 500, color: "#0D1B3E" }}>{s.agent} → {s.step}</span>
                                    <span style={{ fontSize: 11, color: "#9CA3AF" }}>· {s.contact}</span>
                                  </div>
                                  <span style={{ fontSize: 11, color: "#9CA3AF" }}>{s.detail}</span>
                                </div>
                              </div>
                              <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                                <span style={{ fontSize: 10, fontWeight: 600, color: uColor, background: uBg, padding: "2px 8px", borderRadius: 100 }}>{uLabel}</span>
                                <div style={{ textAlign: "right" }}>
                                  <div style={{ fontSize: 12, fontWeight: 600, color: "#0D1B3E" }}>{s.suggestedDate}</div>
                                  <div style={{ fontSize: 10, color: uColor, fontWeight: 500 }}>{s.daysLeft === 1 ? "Tomorrow" : s.daysLeft === 0 ? "Today" : `in ${s.daysLeft} days`}</div>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </>)}

                  {/* BOOKINGS */}
                  {cycle.bookings.length > 0 && (<>
                    <SectionHeader id="bookings" icon={Calendar} title="Calls Booked" count={cycle.bookings.length} color="#C9952A" />
                    {expandedSections.bookings && (
                      <div style={{ marginBottom: 8 }}>
                        {cycle.bookings.map((b, i) => (
                          <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 12px", borderBottom: i < cycle.bookings.length - 1 ? "1px solid rgba(0,0,0,.03)" : "none" }}>
                            <div>
                              <span style={{ fontSize: 13, fontWeight: 500, color: "#0D1B3E" }}>{b.name}</span>
                              <span style={{ fontSize: 11, color: "#9CA3AF" }}> · {b.company}</span>
                            </div>
                            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                              <span style={{ fontSize: 11, color: "#0F7B72", fontWeight: 500 }}>{b.type}</span>
                              <span style={{ fontSize: 11, color: "#6b7b8d" }}>{b.date}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </>)}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div style={{ position: "fixed", bottom: 24, right: 24, width: 56, height: 56, borderRadius: "50%", background: "#0F7B72", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", boxShadow: "0 4px 20px rgba(15,123,114,.3)" }}><MessageCircle size={22} color="white" /></div>
    </div>
  );
}
