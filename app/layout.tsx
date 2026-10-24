import type {Metadata} from 'next';
import './globals.css';
export const metadata:Metadata={title:'Astra | Insider intelligence',description:'Explainable insider risk intelligence and investigation workspace',icons:{icon:'/favicon.svg'}};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>}
