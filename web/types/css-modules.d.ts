// Ambient declarations for CSS Module imports.
// Needed because `npx tsc --noEmit` runs in CI before `next build` — Next.js
// generates these declarations during the build, but type-check happens
// earlier, so without this file TypeScript errors on any `*.module.css`
// import ("Cannot find module './foo.module.css'").
declare module "*.module.css" {
  const classes: { readonly [key: string]: string };
  export default classes;
}

declare module "*.module.scss" {
  const classes: { readonly [key: string]: string };
  export default classes;
}
