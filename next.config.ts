import type {NextConfig} from 'next';
const config:NextConfig={output:'standalone',async rewrites(){return process.env.NEXT_PUBLIC_DEMO_MODE==='false'?[{source:'/api/v1/:path*',destination:(process.env.INTERNAL_API_URL||'http://backend:8000')+'/api/v1/:path*'},{source:'/health',destination:(process.env.INTERNAL_API_URL||'http://backend:8000')+'/health'}]:[]}};
export default config;
