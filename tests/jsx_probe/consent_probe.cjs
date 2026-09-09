/**
 * AST probe for DeployToLivePanel's live-enable consent gate.
 *
 * Parses the real JSX (via the frontend's own @babel/parser) and reports the
 * facts a source grep cannot establish honestly: whether the consent checkbox is
 * rendered UNCONDITIONALLY, and what actually gates the submit button. Emits JSON
 * on stdout for the pytest that drives it.
 *
 * Run: node consent_probe.cjs <path-to-DeployToLivePanel.jsx>
 */
const path = require("path");
const fs = require("fs");

const FRONTEND = path.resolve(__dirname, "..", "..", "frontend");
const parser = require(path.join(FRONTEND, "node_modules", "@babel/parser"));

const file = process.argv[2];
const src = fs.readFileSync(file, "utf8");
const ast = parser.parse(src, {
  sourceType: "module",
  plugins: ["jsx", "classProperties", "optionalChaining", "nullishCoalescingOperator"],
});

const out = {
  consentCheckboxes: [],
  submitDisabledSource: null,
  acceptUnvalidatedSource: null,
  typedConfirmPresent: false,
  // Every "ENABLE" STRING LITERAL in executable code. Comments and JSX text are
  // excluded by construction (the parser never emits them as StringLiteral), so
  // prose describing the removed gate cannot trip the check.
  enableLiterals: [],
};

const slice = (node) => src.slice(node.start, node.end);

// Walk every node, tracking the chain of ancestors so we can ask whether a given
// JSX element sits inside a `{cond && <...>}` conditional.
function walk(node, ancestors) {
  if (!node || typeof node.type !== "string") return;
  visit(node, ancestors);
  const next = ancestors.concat([node]);
  for (const key of Object.keys(node)) {
    if (key === "loc" || key === "start" || key === "end") continue;
    const child = node[key];
    if (Array.isArray(child)) child.forEach((c) => c && typeof c.type === "string" && walk(c, next));
    else if (child && typeof child.type === "string") walk(child, next);
  }
}

const attrOf = (el, name) =>
  (el.openingElement ? el.openingElement.attributes : []).find(
    (a) => a.type === "JSXAttribute" && a.name && a.name.name === name);

// Is this element rendered only when some condition holds? True if any ancestor
// is a `x && <el>` logical expression or a conditional whose branch we are in.
function guardedBy(ancestors) {
  const guards = [];
  for (let i = 0; i < ancestors.length; i++) {
    const a = ancestors[i];
    const child = ancestors[i + 1];
    if (a.type === "LogicalExpression" && a.operator === "&&" && child === a.right) {
      guards.push(slice(a.left));
    }
    if (a.type === "ConditionalExpression" && (child === a.consequent || child === a.alternate)) {
      guards.push(slice(a.test));
    }
  }
  return guards;
}

function visit(node, ancestors) {
  if (node.type === "JSXElement") {
    const nameNode = node.openingElement.name;
    const name = nameNode.type === "JSXIdentifier" ? nameNode.name : null;

    // The consent checkbox: <input type="checkbox" ... data-testid="deploy-to-live-consent">
    if (name === "input") {
      const t = attrOf(node, "type");
      const isCheckbox = t && t.value && t.value.type === "StringLiteral" && t.value.value === "checkbox";
      if (isCheckbox) {
        const tid = attrOf(node, "data-testid");
        out.consentCheckboxes.push({
          testid: tid && tid.value && tid.value.type === "StringLiteral" ? tid.value.value : null,
          guards: guardedBy(ancestors.concat([node])),
        });
      }
      // A typed-confirm text input would be an <input>/<Input> with a value bound
      // to a confirm string; the old one carried this testid.
      const tid = attrOf(node, "data-testid");
      if (tid && tid.value && tid.value.value === "deploy-to-live-confirm-input") out.typedConfirmPresent = true;
    }
    if (name === "Input") {
      const tid = attrOf(node, "data-testid");
      if (tid && tid.value && tid.value.value === "deploy-to-live-confirm-input") out.typedConfirmPresent = true;
    }

    // The submit button's disabled expression.
    const tid = attrOf(node, "data-testid");
    if (tid && tid.value && tid.value.value === "deploy-to-live-arm-submit") {
      const d = attrOf(node, "disabled");
      if (d && d.value && d.value.type === "JSXExpressionContainer") {
        out.submitDisabledSource = slice(d.value.expression);
      }
    }
  }

  if (node.type === "StringLiteral" && node.value === "ENABLE") {
    out.enableLiterals.push(slice(node));
  }

  // The accept_unvalidated_live property in the request body.
  if (node.type === "ObjectProperty" && node.key
      && (node.key.name === "accept_unvalidated_live" || node.key.value === "accept_unvalidated_live")) {
    out.acceptUnvalidatedSource = slice(node.value);
  }
}

walk(ast, []);
process.stdout.write(JSON.stringify(out, null, 2));
