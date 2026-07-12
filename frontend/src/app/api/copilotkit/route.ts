import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { LangGraphAgent, LangGraphHttpAgent } from "@copilotkit/runtime/langgraph";
import { NextRequest } from "next/server";

// Development (langgraph dev): LangGraphAgent talks to the LangGraph platform API
// Production (FastAPI):        LangGraphHttpAgent talks to our AG-UI endpoints
const useProd = !!process.env.USE_PRODUCTION_SERVER;
const agentBase = process.env.AGENT_URL || "http://localhost:8000/copilotkit";
const suggesterBase =
  process.env.SUGGESTER_URL || agentBase.replace(/\/copilotkit$/, "/copilotkit-suggester");

export const POST = async (req: NextRequest) => {
  // Forward the browser's session cookie to the FastAPI backend. The
  // Next.js server is making a server-to-server call and doesn't
  // inherit the browser's cookies automatically — without this, the
  // backend sees user_id="default" for every /copilotkit request and
  // can't claim thread ownership or enforce access.
  const cookie = req.headers.get("cookie") || "";
  const forwarded: Record<string, string> = cookie ? { Cookie: cookie } : {};

  const agent = useProd
    ? new LangGraphHttpAgent({ url: agentBase, headers: forwarded })
    : new LangGraphAgent({
        deploymentUrl: process.env.LANGGRAPH_URL || "http://localhost:2024",
        graphId: "outreach",
        langsmithApiKey: process.env.LANGSMITH_API_KEY || "",
        assistantConfig: { recursion_limit: 100 },
      });

  const suggesterAgent = useProd
    ? new LangGraphHttpAgent({ url: suggesterBase, headers: forwarded })
    : new LangGraphAgent({
        deploymentUrl: process.env.LANGGRAPH_URL || "http://localhost:2024",
        graphId: "suggester",
        langsmithApiKey: process.env.LANGSMITH_API_KEY || "",
        assistantConfig: { recursion_limit: 10 },
      });

  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    endpoint: "/api/copilotkit",
    serviceAdapter: new ExperimentalEmptyAdapter(),
    runtime: new CopilotRuntime({
      agents: { default: agent, suggester: suggesterAgent },
    }),
  });

  return handleRequest(req);
};
