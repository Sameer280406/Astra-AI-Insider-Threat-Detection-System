"use client";
export default function ErrorPage({reset}:{reset:()=>void}){return <main className="error-page"><h1>Something interrupted this workspace.</h1><p>Your saved records are unchanged. Retry to reconnect.</p><button onClick={reset}>Try again</button></main>}
