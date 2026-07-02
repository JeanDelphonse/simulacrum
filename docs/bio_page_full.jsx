import { useState } from "react";
import { MapPin, Linkedin, Globe, Calendar, Heart, MessageCircle, X, Send, Check, ExternalLink, Award, Briefcase, GraduationCap, FileText, BookOpen, Code, Star, Sparkles, ChevronDown, Clock, Users } from "lucide-react";

const T = "#0F7B72", N = "#0D1B3E", G = "#C9952A", M = "#6b7b8d", W = "#FFFFFF";

export default function BioPage() {
  const [liked, setLiked] = useState(false);
  const [likeCount, setLikeCount] = useState(184);
  const [chatOpen, setChatOpen] = useState(false);
  const [expandedSections, setExpandedSections] = useState({ career: true, work: true, ventures: true, services: true, education: true, references: true, publications: true, projects: true, testimonials: true });
  const [messages, setMessages] = useState([{ role: "assistant", text: "Hi! I'm Jean's AI assistant. I can answer questions about his consulting services, AI expertise, and availability. What would you like to know?" }]);
  const [input, setInput] = useState("");

  const toggleLike = () => { setLiked(!liked); setLikeCount(liked ? likeCount - 1 : likeCount + 1); };
  const sendMsg = () => { if (!input.trim()) return; setMessages([...messages, { role: "user", text: input }, { role: "assistant", text: "Great question! Jean specializes in AI program management and has led enterprise-scale implementations at Adobe, Dell, JPMorgan Chase, and Wells Fargo. His most popular engagement is the AI Program Assessment ($8,500) — a 2-week audit with a prioritized roadmap. Would you like to book a discovery call?" }]); setInput(""); };

  const Section = ({ id, title, children, count }) => (
    <section style={{ borderBottom: "1px solid rgba(0,0,0,.06)", padding: "28px 0" }}>
      <div onClick={() => setExpandedSections(p => ({ ...p, [id]: !p[id] }))} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", marginBottom: expandedSections[id] ? 20 : 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <h2 style={{ fontFamily: "'Playfair Display', serif", fontSize: 22, fontWeight: 700, color: N }}>{title}</h2>
          {count && <span style={{ fontSize: 11, color: T, background: "#D6F0EE", padding: "2px 8px", borderRadius: 100, fontWeight: 500 }}>{count}</span>}
        </div>
        <ChevronDown size={18} color={M} style={{ transform: expandedSections[id] ? "rotate(180deg)" : "none", transition: "transform .2s" }} />
      </div>
      {expandedSections[id] && children}
    </section>
  );

  return (
    <div style={{ fontFamily: "'DM Sans', sans-serif", background: "#FAFBFC", minHeight: "100vh" }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=Playfair+Display:wght@400;600;700&display=swap');*{box-sizing:border-box;margin:0;padding:0}.svc-card{transition:all .2s}.svc-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.08)}.work-card{transition:all .15s}.work-card:hover{border-left-color:#0F7B72 !important}.venture-card{transition:all .2s;cursor:pointer}.venture-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.08)}.ref-row{transition:background .1s}.ref-row:hover{background:rgba(0,0,0,.01)}.proj-card{transition:all .2s}.proj-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.08)}`}</style>

      <div style={{ maxWidth: 800, margin: "0 auto", padding: "0 24px" }}>

        {/* ═══ 1. HERO ═══ */}
        <section style={{ padding: "48px 0 24px", borderBottom: "1px solid rgba(0,0,0,.06)" }}>
          <div style={{ display: "flex", gap: 24, alignItems: "flex-start" }}>
            <div style={{ width: 100, height: 100, borderRadius: "50%", background: `linear-gradient(135deg, ${N}, ${T})`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
              <span style={{ fontFamily: "'Playfair Display', serif", fontSize: 36, fontWeight: 700, color: W }}>JD</span>
            </div>
            <div style={{ flex: 1 }}>
              <h1 style={{ fontFamily: "'Playfair Display', serif", fontSize: 32, fontWeight: 700, color: N, marginBottom: 4 }}>Jean M. Delphonse</h1>
              <p style={{ fontSize: 16, color: T, fontWeight: 500, marginBottom: 8 }}>AI Program Management & Enterprise Data Systems</p>
              <div style={{ display: "flex", gap: 16, alignItems: "center", fontSize: 13, color: M, marginBottom: 16, flexWrap: "wrap" }}>
                <span style={{ display: "flex", alignItems: "center", gap: 4 }}><MapPin size={13} /> Santa Clara, CA</span>
                <span style={{ display: "flex", alignItems: "center", gap: 4 }}><Briefcase size={13} /> 15+ years enterprise</span>
                <a href="#" style={{ display: "flex", alignItems: "center", gap: 4, color: M, textDecoration: "none" }}><Linkedin size={13} /> LinkedIn</a>
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <button style={{ display: "flex", alignItems: "center", gap: 8, background: T, border: "none", borderRadius: 8, padding: "10px 20px", fontSize: 14, fontWeight: 600, color: W, cursor: "pointer" }}><Calendar size={16} /> Book a call</button>
                <button onClick={toggleLike} style={{ display: "flex", alignItems: "center", gap: 6, background: "white", border: `1px solid ${liked ? T : "#d0d5dd"}`, borderRadius: 8, padding: "10px 16px", fontSize: 13, fontWeight: 500, color: liked ? T : M, cursor: "pointer", transition: "all .2s" }}>
                  <Heart size={15} fill={liked ? T : "none"} color={liked ? T : M} /> {likeCount}
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* ═══ 2. ABOUT ═══ */}
        <section style={{ padding: "28px 0", borderBottom: "1px solid rgba(0,0,0,.06)" }}>
          <h2 style={{ fontFamily: "'Playfair Display', serif", fontSize: 22, fontWeight: 700, color: N, marginBottom: 16 }}>About</h2>
          <p style={{ fontSize: 15, color: "#4a5568", lineHeight: 1.8 }}>I help engineering leaders build AI programs that survive their first year. 15+ years of enterprise experience spanning technical program management, data engineering, and AI/ML systems at Adobe, Dell Technologies, JPMorgan Chase, and Wells Fargo. I have seen what works at scale and what collapses under its own complexity.</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 16 }}>
            {["AI/ML Systems", "Technical Program Management", "Data Engineering", "Enterprise Architecture", "Agentic AI"].map((t, i) => (
              <span key={i} style={{ padding: "4px 12px", borderRadius: 100, background: "#D6F0EE", fontSize: 12, color: T, fontWeight: 500 }}>{t}</span>
            ))}
          </div>
        </section>

        {/* ═══ 3. CAREER TIMELINE ═══ */}
        <Section id="career" title="Career" count="4 roles">
          <div style={{ position: "relative", paddingLeft: 28 }}>
            <div style={{ position: "absolute", left: 7, top: 8, bottom: 8, width: 2, background: "#E0E0E0" }} />
            {[
              { company: "Independent Consultant", url: "https://simulacrumai.io", role: "AI Program Management & Data Engineering", dates: "2023 – Present", desc: "Building AI-native platforms: Simulacrum (career wealth simulation), Bay Area Experiences (tourism marketplace), PrezentEnergy Ventures (EV charging). Claude API, Flask, orchestrator engineering." },
              { company: "Adobe", url: "https://adobe.com", role: "Technical Program Manager", dates: "2019 – 2023 · 4 years", desc: "Led cross-functional data engineering programs across Adobe Experience Cloud. Managed $4M+ annual program budgets and 30+ person distributed teams." },
              { company: "Dell Technologies", url: "https://dell.com", role: "Senior Data Engineer", dates: "2015 – 2019 · 4 years", desc: "Built enterprise data pipelines processing 2B+ records/day. Led migration from legacy ETL to cloud-native architecture on AWS." },
              { company: "JPMorgan Chase", url: "https://jpmorganchase.com", role: "Data Governance & Engineering", dates: "2010 – 2015 · 5 years", desc: "Enterprise data governance across investment banking platforms. SOX compliance, data lineage, quality frameworks." },
            ].map((c, i) => (
              <div key={i} style={{ position: "relative", marginBottom: 24 }}>
                <div style={{ position: "absolute", left: -24, top: 6, width: 12, height: 12, borderRadius: "50%", background: i === 0 ? T : W, border: `2px solid ${T}` }} />
                <div style={{ marginBottom: 4 }}>
                  <a href={c.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 16, fontWeight: 600, color: N, textDecoration: "none" }}>{c.company} <ExternalLink size={12} color={T} style={{ verticalAlign: "middle" }} /></a>
                </div>
                <div style={{ fontSize: 14, color: T, fontWeight: 500, marginBottom: 2 }}>{c.role}</div>
                <div style={{ fontSize: 12, color: M, marginBottom: 6 }}>{c.dates}</div>
                <p style={{ fontSize: 13, color: "#4a5568", lineHeight: 1.6 }}>{c.desc}</p>
              </div>
            ))}
          </div>
        </Section>

        {/* ═══ 4. NOTABLE WORK ═══ */}
        <Section id="work" title="Notable Work" count="5">
          {[
            { title: "Built Simulacrum's 49-Agent Orchestrator", desc: "Designed and implemented a Bayesian orchestrator that dispatches 49 AI agents across 5 wealth layers on 24-hour autonomous cycles. Explore/exploit strategy inspired by Algorithms to Live By.", company: "Simulacrum", link: "https://simulacrumai.io" },
            { title: "Scaled Adobe Experience Cloud Data Pipeline", desc: "Led the re-architecture of Adobe's data pipeline from batch to real-time, processing 2.4B events/day with 99.97% uptime. Reduced data latency from 24 hours to under 15 minutes.", company: "Adobe" },
            { title: "Built KrikKrak.ai Educational AI Platform", desc: "Created an LLM data augmentation platform for culturally adaptive educational content. RAG architecture with Pinecone, Hugging Face, and a self-evaluation/conditional retry loop.", company: "ColloquyAI" },
            { title: "Enterprise Data Governance Framework at JPMC", desc: "Designed a 6-layer data governance framework adopted across 3 LOBs at JPMorgan Chase. Achieved SOX compliance for 400+ data assets.", company: "JPMorgan Chase" },
            { title: "Bay Area Experiences Tourism Marketplace", desc: "Built a private small-group tour marketplace serving 13 Bay Area cities. 9 experiences, Stripe payments, provider management system. Flask + SQLAlchemy + Bootstrap 5.", company: "Bay Area Experiences", link: "https://bayareaexperiences.com" },
          ].map((w, i) => (
            <div key={i} className="work-card" style={{ background: "white", borderRadius: 10, padding: "16px 20px", marginBottom: 8, border: "1px solid rgba(0,0,0,.06)", borderLeft: "3px solid #E0E0E0", transition: "border-left-color .2s" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div>
                  <h3 style={{ fontSize: 15, fontWeight: 600, color: N, marginBottom: 4 }}>{w.title}</h3>
                  <p style={{ fontSize: 13, color: "#4a5568", lineHeight: 1.6, marginBottom: 6 }}>{w.desc}</p>
                  <span style={{ fontSize: 11, color: M }}>at {w.company}</span>
                </div>
                {w.link && <a href={w.link} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: T, textDecoration: "none", fontWeight: 500, whiteSpace: "nowrap", marginLeft: 12 }}>View →</a>}
              </div>
            </div>
          ))}
        </Section>

        {/* ═══ 5. VENTURES ═══ */}
        <Section id="ventures" title="Ventures" count="3">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {[
              { name: "Simulacrum", url: "https://simulacrumai.io", role: "Founder & CEO", desc: "AI-powered career wealth simulation platform. 49 agents, 5 income layers, Bayesian orchestrator.", status: "Active", color: "#1A8754" },
              { name: "Bay Area Experiences", url: "https://bayareaexperiences.com", role: "Founder & Lead Guide", desc: "Private small-group tour and transportation marketplace for 13 Bay Area cities.", status: "Active", color: "#1A8754" },
              { name: "PrezentEnergy Ventures", url: null, role: "Co-Founder & CEO", desc: "Mobile workplace EV charging startup. Charging-as-a-Service targeting Santa Clara County corporates.", status: "Active", color: "#1A8754" },
            ].map((v, i) => (
              <a key={i} href={v.url || "#"} target={v.url ? "_blank" : "_self"} rel="noopener noreferrer" className="venture-card" style={{ background: "white", borderRadius: 12, padding: "20px", border: "1px solid rgba(0,0,0,.06)", textDecoration: "none", display: "block" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <h3 style={{ fontSize: 15, fontWeight: 600, color: N }}>{v.name}</h3>
                  <span style={{ fontSize: 10, fontWeight: 600, color: v.color, background: `${v.color}15`, padding: "2px 8px", borderRadius: 100 }}>{v.status}</span>
                </div>
                <p style={{ fontSize: 12, color: T, fontWeight: 500, marginBottom: 6 }}>{v.role}</p>
                <p style={{ fontSize: 12, color: M, lineHeight: 1.5 }}>{v.desc}</p>
                {v.url && <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 8, fontSize: 11, color: T }}><Globe size={11} /> {v.url.replace("https://","")}</div>}
              </a>
            ))}
          </div>
        </Section>

        {/* ═══ 6. SERVICES ═══ */}
        <Section id="services" title="Services" count="4 tiers">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {[
              { name: "Advisory Call", price: "$350", dur: "60 min", desc: "For founders and VPs evaluating AI program investments" },
              { name: "AI Program Assessment", price: "$8,500", dur: "2 weeks", desc: "Full audit of your AI program with a prioritized roadmap" },
              { name: "Fractional AI TPM", price: "$12,000/mo", dur: "Ongoing", desc: "Embedded in your team 10-15 hrs/week as your AI program lead" },
              { name: "Full Program Build", price: "$45,000", dur: "3 months", desc: "Stand up an AI program from scratch — strategy through execution" },
            ].map((s, i) => (
              <div key={i} className="svc-card" style={{ background: "white", borderRadius: 12, padding: "20px", border: "1px solid rgba(0,0,0,.06)", cursor: "pointer" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <h3 style={{ fontSize: 14, fontWeight: 600, color: N }}>{s.name}</h3>
                  <span style={{ fontSize: 14, fontWeight: 700, color: T }}>{s.price}</span>
                </div>
                <p style={{ fontSize: 12, color: M, lineHeight: 1.5, marginBottom: 6 }}>{s.desc}</p>
                <span style={{ fontSize: 11, color: "#b0b8c4" }}>{s.dur}</span>
              </div>
            ))}
          </div>
        </Section>

        {/* ═══ 7. EDUCATION ═══ */}
        <Section id="education" title="Education & Certifications">
          <div style={{ marginBottom: 16 }}>
            {[
              { inst: "Stanford University", url: "https://stanford.edu", degree: "Continuing Education — AI/ML", year: "" },
              { inst: "Oregon State University", url: "https://oregonstate.edu", degree: "BS Computer Science", year: "" },
            ].map((e, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 0", borderBottom: i === 0 ? "1px solid rgba(0,0,0,.04)" : "none" }}>
                <div style={{ width: 40, height: 40, borderRadius: 10, background: "#F0F4F8", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <GraduationCap size={18} color={T} />
                </div>
                <div>
                  <a href={e.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 14, fontWeight: 600, color: N, textDecoration: "none" }}>{e.inst} <ExternalLink size={10} color={T} /></a>
                  <div style={{ fontSize: 13, color: M }}>{e.degree}</div>
                </div>
              </div>
            ))}
          </div>
          <div>
            <h3 style={{ fontSize: 13, fontWeight: 600, color: M, textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>Certifications</h3>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {["Claude API / Agentic AI", "Flask + SQLAlchemy", "AWS Solutions Architect", "PMP"].map((c, i) => (
                <span key={i} style={{ padding: "5px 14px", borderRadius: 8, background: "white", border: "1px solid rgba(0,0,0,.06)", fontSize: 12, color: N, fontWeight: 500 }}>{c}</span>
              ))}
            </div>
          </div>
        </Section>

        {/* ═══ 8. REFERENCES & PRESS ═══ */}
        <Section id="references" title="In the Press" count="3">
          {[
            { title: "How AI Agents Are Reshaping Career Development", pub: "AI Business Weekly", date: "May 2026", quote: "Delphonse's Simulacrum platform takes a fundamentally different approach — reading careers instead of resumes." },
            { title: "The Rise of Fractional Technical Leadership", pub: "CTO Craft", date: "March 2026", quote: "Former enterprise leaders like Delphonse are bringing Fortune 500 discipline to startups at a fraction of the cost." },
            { title: "Bay Area's Hidden Tour Experiences", pub: "SF Chronicle Travel", date: "January 2026", quote: "Bay Area Experiences offers intimate, small-group tours that the big operators can't match." },
          ].map((r, i) => (
            <div key={i} className="ref-row" style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "12px 0", borderBottom: i < 2 ? "1px solid rgba(0,0,0,.04)" : "none" }}>
              <div style={{ width: 32, height: 32, borderRadius: 8, background: "#F0F4F8", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 2 }}>
                <FileText size={14} color={T} />
              </div>
              <div>
                <a href="#" style={{ fontSize: 14, fontWeight: 600, color: N, textDecoration: "none" }}>{r.title} <ExternalLink size={10} color={T} /></a>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 2 }}>
                  <span style={{ fontSize: 12, color: T, fontWeight: 500 }}>{r.pub}</span>
                  <span style={{ fontSize: 11, color: "#b0b8c4" }}>· {r.date}</span>
                </div>
                <p style={{ fontSize: 12, color: M, fontStyle: "italic", marginTop: 6, lineHeight: 1.5 }}>"{r.quote}"</p>
              </div>
            </div>
          ))}
        </Section>

        {/* ═══ 9. PUBLICATIONS & SPEAKING ═══ */}
        <Section id="publications" title="Publications & Speaking" count="4">
          {[
            { title: "Building AI Programs That Survive Year One", venue: "AI Engineering Summit 2026", type: "Talk", color: G },
            { title: "The 49-Agent Architecture: Orchestrating AI at Scale", venue: "Simulacrum Engineering Blog", type: "Article", color: T },
            { title: "Algorithms to Live By — Applied to Product Development", venue: "Product Leaders Podcast", type: "Podcast", color: "#534AB7" },
            { title: "From Data Engineer to AI Entrepreneur", venue: "Dell Alumni Network Keynote", type: "Talk", color: G },
          ].map((p, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 0", borderBottom: i < 3 ? "1px solid rgba(0,0,0,.04)" : "none" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 9, fontWeight: 600, color: p.color, background: `${p.color}15`, padding: "2px 8px", borderRadius: 4, textTransform: "uppercase" }}>{p.type}</span>
                <div>
                  <a href="#" style={{ fontSize: 14, fontWeight: 500, color: N, textDecoration: "none" }}>{p.title}</a>
                  <div style={{ fontSize: 12, color: M }}>{p.venue}</div>
                </div>
              </div>
              <ExternalLink size={13} color={T} />
            </div>
          ))}
        </Section>

        {/* ═══ 10. PROJECT LINKS ═══ */}
        <Section id="projects" title="Projects" count="3">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
            {[
              { name: "Simulacrum Platform", url: "simulacrumai.io", desc: "AI career wealth simulation", tags: ["Flask", "Claude API", "MySQL"] },
              { name: "Bay Area Experiences", url: "bayareaexperiences.com", desc: "Tourism marketplace", tags: ["Python", "Stripe", "Bootstrap"] },
              { name: "Selene Space", url: null, desc: "Cislunar data center intelligence", tags: ["LangGraph", "Flask", "SQLite"] },
            ].map((p, i) => (
              <a key={i} href={p.url ? `https://${p.url}` : "#"} target="_blank" rel="noopener noreferrer" className="proj-card" style={{ background: "white", borderRadius: 10, padding: "16px", border: "1px solid rgba(0,0,0,.06)", textDecoration: "none", display: "block" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
                  <Code size={14} color={T} />
                  <h3 style={{ fontSize: 13, fontWeight: 600, color: N }}>{p.name}</h3>
                </div>
                <p style={{ fontSize: 12, color: M, marginBottom: 10, lineHeight: 1.4 }}>{p.desc}</p>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {p.tags.map((t, j) => <span key={j} style={{ fontSize: 10, color: T, background: "#D6F0EE", padding: "2px 8px", borderRadius: 4 }}>{t}</span>)}
                </div>
                {p.url && <div style={{ fontSize: 10, color: M, marginTop: 8, display: "flex", alignItems: "center", gap: 3 }}><Globe size={10} /> {p.url}</div>}
              </a>
            ))}
          </div>
        </Section>

        {/* ═══ 11. TESTIMONIALS ═══ */}
        <Section id="testimonials" title="What Clients Say" count="2">
          {[
            { name: "Ananya Patel", title: "VP Engineering, FinTech Co", text: "Jean restructured our entire AI pipeline in 6 weeks. The cross-functional operating model he built is still running perfectly 18 months later." },
            { name: "Carlos Marin", title: "CTO, HealthTech Inc", text: "We needed someone who understood both the technical architecture and the organizational politics. Jean delivered on both." },
          ].map((t, i) => (
            <div key={i} style={{ background: "white", borderRadius: 10, padding: "16px 20px", marginBottom: 8, border: "1px solid rgba(0,0,0,.06)", borderLeft: `3px solid ${T}` }}>
              <p style={{ fontSize: 13, color: "#4a5568", lineHeight: 1.7, marginBottom: 10, fontStyle: "italic" }}>"{t.text}"</p>
              <div style={{ fontSize: 13, fontWeight: 600, color: N }}>{t.name}</div>
              <div style={{ fontSize: 12, color: M }}>{t.title}</div>
            </div>
          ))}
        </Section>

        {/* ═══ FOOTER ═══ */}
        <footer style={{ padding: "28px 0 48px", textAlign: "center" }}>
          <div style={{ display: "flex", justifyContent: "center", gap: 16, marginBottom: 20 }}>
            <a href="#" style={{ color: M, textDecoration: "none", fontSize: 13 }}>LinkedIn</a>
            <a href="https://simulacrumai.io" style={{ color: M, textDecoration: "none", fontSize: 13 }}>simulacrumai.io</a>
            <a href="mailto:simi@simulacrumai.io" style={{ color: M, textDecoration: "none", fontSize: 13 }}>simi@simulacrumai.io</a>
          </div>
          <a href="https://simulacrumai.io?src=bio_badge" style={{ display: "inline-flex", alignItems: "center", gap: 8, textDecoration: "none", padding: "8px 16px", borderRadius: 100, background: "rgba(0,0,0,.02)", border: "1px solid rgba(0,0,0,.04)" }}>
            <div style={{ width: 16, height: 16, borderRadius: 4, background: `linear-gradient(135deg, ${N}, ${T})`, display: "flex", alignItems: "center", justifyContent: "center" }}><Sparkles size={8} color={W} /></div>
            <span style={{ fontSize: 13, color: "#9CA3AF" }}>Built with Simulacrum</span>
            <span style={{ fontSize: 13, color: T, fontWeight: 500 }}>· Get yours free →</span>
          </a>
          <div style={{ fontSize: 12, color: "#d0d5dd", marginTop: 16 }}>© 2026 Jean M. Delphonse</div>
        </footer>
      </div>

      {/* ═══ CHAT WIDGET ═══ */}
      {!chatOpen && <div onClick={() => setChatOpen(true)} style={{ position: "fixed", bottom: 24, right: 24, width: 56, height: 56, borderRadius: "50%", background: T, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", boxShadow: `0 4px 20px ${T}40`, zIndex: 100 }}><MessageCircle size={22} color={W} /></div>}
      {chatOpen && (
        <div style={{ position: "fixed", bottom: 24, right: 24, width: 380, height: 480, borderRadius: 16, background: W, boxShadow: "0 8px 40px rgba(0,0,0,.15)", display: "flex", flexDirection: "column", overflow: "hidden", zIndex: 100, border: "1px solid rgba(0,0,0,.08)" }}>
          <div style={{ padding: "12px 16px", background: N, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ width: 30, height: 30, borderRadius: "50%", background: `linear-gradient(135deg, ${T}, #13A89E)`, display: "flex", alignItems: "center", justifyContent: "center" }}><span style={{ fontSize: 11, fontWeight: 700, color: W }}>JD</span></div>
              <div><div style={{ fontSize: 13, fontWeight: 600, color: W }}>Chat with Jean's AI</div><div style={{ fontSize: 10, color: "rgba(255,255,255,.5)" }}>Responds instantly</div></div>
            </div>
            <button onClick={() => setChatOpen(false)} style={{ background: "none", border: "none", cursor: "pointer" }}><X size={16} color="rgba(255,255,255,.6)" /></button>
          </div>
          <div style={{ flex: 1, overflow: "auto", padding: "14px" }}>
            {messages.map((m, i) => (
              <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", marginBottom: 10 }}>
                <div style={{ maxWidth: "80%", padding: "10px 14px", borderRadius: 12, background: m.role === "user" ? T : "#F0F4F8", color: m.role === "user" ? W : "#2D3748", fontSize: 13, lineHeight: 1.6, borderBottomRightRadius: m.role === "user" ? 4 : 12, borderBottomLeftRadius: m.role === "user" ? 12 : 4 }}>{m.text}</div>
              </div>
            ))}
          </div>
          <div style={{ padding: "10px 14px", borderTop: "1px solid rgba(0,0,0,.06)" }}>
            <div style={{ display: "flex", gap: 8 }}>
              <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === "Enter" && sendMsg()} placeholder="Ask about services, expertise..." style={{ flex: 1, padding: "10px 12px", borderRadius: 8, border: "1px solid #d0d5dd", fontSize: 13, outline: "none" }} />
              <button onClick={sendMsg} style={{ width: 38, height: 38, borderRadius: 8, background: T, border: "none", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}><Send size={14} color={W} /></button>
            </div>
            <a href="https://simulacrumai.io?src=chat_widget" style={{ display: "block", textAlign: "center", marginTop: 6, fontSize: 10, color: "#b0b8c4", textDecoration: "none" }}>Powered by <span style={{ color: "#9CA3AF", fontWeight: 500 }}>Simulacrum AI</span></a>
          </div>
        </div>
      )}
    </div>
  );
}
